import threading
import time
import json
import uuid
import logging
import requests
import boto3
import secrets
import string
import base64
import hmac
import hashlib
from warrant.aws_srp import AWSSRP, get_random, pad_hex, hash_sha256, hex_hash, hex_to_long
from AWSIoTPythonSDK.MQTTLib import AWSIoTMQTTClient
from homeassistant.helpers.dispatcher import async_dispatcher_send
import os
import asyncio
import functools
import time
from .devices_enum import DeviceConfig
from .const import REGION, USER_POOL_ID, ID_USER_POOL, CLIENT_ID, CLIENT_SECRET, IDENTITY_POOL_ID, IOT_HOST
from .trapezoidal_riemann_sum import TrapezoidalRiemannSum

_LOGGER = logging.getLogger(__name__)

class EdpSolarApi:
    def __init__(self, username, password, hass=None):
        self.username = username
        self.password = password
        self.hass = hass
        self._lock = threading.Lock()
        self._mqtt_client = None

        # State variables for sensors
        self.instant_power_produced = 0
        self.instant_power_consumed = None
        self.instant_power_from_grid = 0
        self.instant_power_injected = 0
        self.available_device_ids = []
        self.available_devices = {}
        self.house_id = None
        self.user_id = None
        
        self.energy_consumed = TrapezoidalRiemannSum()
        self.energy_produced = TrapezoidalRiemannSum()
        self.energy_from_grid = TrapezoidalRiemannSum()
        self.energy_injected = TrapezoidalRiemannSum()

        self._stop_event = threading.Event()
        self._mqtt_thread = None

        self.mqttRefresh = 0
        self.mqqtRefreshPeriod = 600

        # Authentication variables
        self.access_token = None
        self.id_token = None
        self.refresh_token = None
        self.device_key = None
        self.device_group_key = None
        self.identity_id = None
        self.access_key = None
        self.secret_key = None
        self.session_token = None
        self.experiation = None

    def start(self):
        """Start the authentication and MQTT subscription process in a background thread."""
        self._mqtt_thread = threading.Thread(target=self._run, daemon=True)
        self._mqtt_thread.start()

    def stop(self):
        """Stop the MQTT client and background thread."""
        self._stop_event.set()
        if self._mqtt_client:
            self._mqtt_client.disconnect()
        if self._mqtt_thread:
            self._mqtt_thread.join(timeout=5)

    def _run(self):
        try:
            self.hass.loop.call_soon_threadsafe(
                lambda: self.hass.async_create_task(self._authenticate_and_subscribe())
            )
        except Exception as ex:
            _LOGGER.error("EDP Solar API thread crashed: %s", ex, exc_info=True)

    async def async_send_signal(self):
        try:
            from homeassistant.helpers.dispatcher import async_dispatcher_send
            async_dispatcher_send(self.hass, "edp_solar_update")
        except Exception as e:
            _LOGGER.error("Signal dispatch failed: %s", e)

    # --- Helper functions ---
    @staticmethod
    def get_secret_hash(username, client_id, client_secret):
            message = username + client_id
            dig = hmac.new(
                client_secret.encode('utf-8'),
                msg=message.encode('utf-8'),
                digestmod=hashlib.sha256
            ).digest()
            return base64.b64encode(dig).decode()
    @staticmethod
    def generate_random_device_password(length=16):
            letters = string.ascii_letters
            digits = string.digits
            symbols = '!@#$%^&*()_+-=[]{}|;:,.<>?'
            all_chars = letters + digits + symbols
            password = [
                secrets.choice(letters),
                secrets.choice(digits),
                secrets.choice(symbols)
            ] + [secrets.choice(all_chars) for _ in range(length-3)]
            secrets.SystemRandom().shuffle(password)
            return ''.join(password)
    @staticmethod
    def generate_device_secret_verifier(username, device_key, device_group_key, device_password, pool_id, client_id, client):
            device_and_pw = f"{device_group_key}{device_key}:{device_password}"
            device_and_pw_hash = hash_sha256(device_and_pw.encode("utf-8"))
            salt = pad_hex(get_random(16))
            x_value = hex_to_long(hex_hash(salt + device_and_pw_hash))
            srp_helper = AWSSRP(
                username=username,
                password=device_password,
                pool_id=pool_id,
                client_id=client_id,
                client=client
            )
            verifier = pad_hex(pow(srp_helper.g, x_value, srp_helper.big_n))
            return {
                "PasswordVerifier": base64.standard_b64encode(bytearray.fromhex(verifier)).decode("utf-8"),
                "Salt": base64.standard_b64encode(bytearray.fromhex(salt)).decode("utf-8"),
            }
        
    def auth(self):
            # --- Step 1: Cognito Authentication ---
            _LOGGER.debug("Starting auth")
            cognito_idp = boto3.client('cognito-idp', region_name=REGION)
            #cognito_idp = await self.hass.async_add_executor_job(functools.partial(boto3.client, 'cognito-idp', region_name=REGION))
            cognito_identity = boto3.client('cognito-identity', region_name=REGION)
            #cognito_identity = await self.hass.async_add_executor_job(functools.partial(boto3.client, 'cognito-identity', region_name=REGION))
            secret_hash = EdpSolarApi.get_secret_hash(self.username, CLIENT_ID, CLIENT_SECRET)
            auth_response = cognito_idp.initiate_auth(
                AuthFlow='USER_PASSWORD_AUTH',
                AuthParameters={
                    'USERNAME': self.username,
                    'PASSWORD': self.password,
                    'SECRET_HASH': secret_hash
                },
                ClientId=CLIENT_ID
            )
            with self._lock:
                self.access_token = auth_response['AuthenticationResult']['AccessToken']
                self.id_token = auth_response['AuthenticationResult']['IdToken']
                self.refresh_token = auth_response['AuthenticationResult']['RefreshToken']
                self.device_key = auth_response['AuthenticationResult']['NewDeviceMetadata']['DeviceKey']
                self.device_group_key = auth_response['AuthenticationResult']['NewDeviceMetadata']['DeviceGroupKey']
                self.experiation = auth_response['AuthenticationResult']['ExpiresIn']

            device_secret_verifier = EdpSolarApi.generate_device_secret_verifier(
                self.username, self.device_key, self.device_group_key, EdpSolarApi.generate_random_device_password(),
                USER_POOL_ID, CLIENT_ID, cognito_idp
            )
            cognito_idp.confirm_device(
                AccessToken=self.access_token,
                DeviceKey=self.device_key,
                DeviceSecretVerifierConfig={
                    'PasswordVerifier': device_secret_verifier['PasswordVerifier'],
                    'Salt': device_secret_verifier['Salt']
                },
                DeviceName='homeassistant'
            )
            # Step 2: Get AWS Identity and Credentials
            get_id_response = cognito_identity.get_id(
                IdentityPoolId=IDENTITY_POOL_ID,
                Logins={f'cognito-idp.{REGION}.amazonaws.com/{ID_USER_POOL}': self.id_token}
            )
            with self._lock:
                self.identity_id = get_id_response['IdentityId']
            creds_response = cognito_identity.get_credentials_for_identity(
                IdentityId=self.identity_id,
                Logins={f'cognito-idp.{REGION}.amazonaws.com/{ID_USER_POOL}': self.id_token}
            )
            with self._lock:
                self.access_key = creds_response['Credentials']['AccessKeyId']
                self.secret_key = creds_response['Credentials']['SecretKey']
                self.session_token = creds_response['Credentials']['SessionToken']

            # Step 3: Get User ID
            user_response = cognito_idp.get_user(AccessToken=self.access_token)
            with self._lock:
                self.user_id = user_response['Username']

            # 4. RevokeToken
            cognito_idp.revoke_token(
                Token=self.refresh_token,
                ClientId=CLIENT_ID,
                ClientSecret=CLIENT_SECRET
            )

            # 5. InitiateAuth again (using username and password)
            secret_hash = EdpSolarApi.get_secret_hash(self.user_id, CLIENT_ID, CLIENT_SECRET)
            auth_response2 = cognito_idp.initiate_auth(
                AuthFlow='USER_PASSWORD_AUTH',
                AuthParameters={
                    'USERNAME': self.user_id,
                    'PASSWORD': self.password,
                    'SECRET_HASH': secret_hash
                },
                ClientId=CLIENT_ID
            )
            with self._lock:
                self.access_token = auth_response2['AuthenticationResult']['AccessToken']
                self.id_token = auth_response2['AuthenticationResult']['IdToken']
                self.refresh_token = auth_response2['AuthenticationResult']['RefreshToken']
                self.device_key = auth_response2['AuthenticationResult']['NewDeviceMetadata']['DeviceKey']
                self.device_group_key = auth_response2['AuthenticationResult']['NewDeviceMetadata']['DeviceGroupKey']
            # 7. ConfirmDevice again
            device_secret_verifier = EdpSolarApi.generate_device_secret_verifier(
                self.user_id, self.device_key, self.device_group_key, EdpSolarApi.generate_random_device_password(),
                USER_POOL_ID, CLIENT_ID, cognito_idp
            )

            cognito_idp.confirm_device(
                AccessToken=self.access_token,
                DeviceKey=self.device_key,
                DeviceSecretVerifierConfig={
                    'PasswordVerifier': device_secret_verifier['PasswordVerifier'],
                    'Salt': device_secret_verifier['Salt']
                },
                DeviceName='homeassistant'
            )

            # 8. GetId again
            get_id_response2 = cognito_identity.get_id(
            IdentityPoolId=IDENTITY_POOL_ID,
            Logins={
                    f'cognito-idp.{REGION}.amazonaws.com/{ID_USER_POOL}': self.id_token
                }
            )
            with self._lock:
                self.identity_id = get_id_response2['IdentityId']

            # 9. GetCredentialsForIdentity again
            creds_response2 = cognito_identity.get_credentials_for_identity(
            IdentityId=self.identity_id,
            Logins={
                f'cognito-idp.{REGION}.amazonaws.com/{ID_USER_POOL}': self.id_token
                }
            )

            # 10. GetUser (using access token)
            user_response = cognito_idp.get_user(
                AccessToken=self.access_token
            )
        
    #Used to maintain cognito credentials up to date
    async def periodic_cognito(self):
            while True:
                print("Async task executed!")
                await asyncio.sleep(3600)
                await self.hass.async_add_executor_job(self.auth)
        
    async def _async_retrieve_devices_and_modules(self):
            _LOGGER.debug("Retrieving Houses")
            loop = asyncio.get_running_loop()
            # Step 4: Get House ID
            url = 'https://uiapi.emcp.edp.com/equipment/houses'
            headers = {
                'Accept-Encoding': 'gzip',
                'Authorization': self.id_token,
                'Connection': 'Keep-Alive',
                'Content-Type': 'application/json',
                'Host': 'uiapi.emcp.edp.com',
                'User-Agent': 'okhttp/5.0.0-alpha.14'
            }
            response = await loop.run_in_executor(None, functools.partial(requests.get, url,headers = headers))#requests.get(url,headers= headers) #
            house = response.json()
            house_id = house["houses"][0]["houseId"]

            # Step 5: Get Devices and Modules
            _LOGGER.debug("Retrieving Devices")
            url = f'https://uiapi.emcp.edp.com/equipment/houses/{house_id}/device'
            devices_response = await loop.run_in_executor(None, functools.partial(requests.get, url,headers = headers))
            devices = devices_response.json()

            device_ids = [device["deviceLocalId"] for device in devices]
            #device_deviceId[device["deviceId"]: device for device in devices]

            url = f'https://uiapi.emcp.edp.com/equipment/houses/{house_id}/modules'
            modules_response = await loop.run_in_executor(None, functools.partial(requests.get, url,headers = headers))
            modules = modules_response.json()
            module_map = {module['deviceId']: module for module in modules['Modules']}
            available_devices = {}
            for device in devices:                
                device_id = device['deviceId']
                module = module_map.get(device_id)
                device_type = DeviceConfig.NOT_CONFIGURED.name
                if module:
                    groups = module.get('groups', [])
                    if 'PRODUCTION_METER' in groups:
                        device_type = DeviceConfig.PRODUCTION.name
                    elif 'CONSUMPTION_METER' in groups:
                        device_type = DeviceConfig.GRID.name
                available_devices[device["deviceLocalId"]] = {
                        "device_id": device["deviceId"],
                        "deviceLocalId": device["deviceLocalId"],
                        "type": device["type"],
                        "device_type": device_type,
                        "status": module.get('connectivityState'),
                        "serialNumber": module.get('serialNumber')
                    }

            #device_ids = [device["deviceLocalId"] for device in devices]
            _LOGGER.debug(available_devices)
            # Store state
            with self._lock:
                self.available_device_ids = device_ids
                self.available_devices = available_devices
                self.house_id = house_id
                #self.user_id = user_id

    def custom_disconnect_callback(client, userdata, rc):
            _LOGGER.debug(f"Disconnected from AWS IoT Core with result code: {rc}")
            if rc != 0:
                _LOGGER.debug("Unexpected disconnect. Attempting to reconnect...")
                self._mqtt_client.configureIAMCredentials(self.access_key, self.secret_key, self.session_token)
                self._mqtt_client.connect()

    def _setup_mqtt(self):
            _LOGGER.debug("Starting MQTT")
            # Step 6: Setup MQTT
            current_dir = os.path.dirname(os.path.abspath(__file__))
            ca_path = os.path.join(current_dir, 'certificates/AmazonRootCA1.pem')
            mqtt_client = AWSIoTMQTTClient(str(uuid.uuid4()), useWebsocket=True)
            mqtt_client.configureEndpoint(IOT_HOST, 443)
            mqtt_client.configureCredentials(ca_path)
            mqtt_client.configureIAMCredentials(self.access_key, self.secret_key, self.session_token)
            mqtt_client.connect()
            self._mqtt_client = mqtt_client

    def custom_callback(self, client, userdata, message):
            payload = json.loads(message.payload.decode())
            if message.topic.endswith("/fromDev/realtime") and 'data' in payload and len(payload['data']) > 0:
                device = None
                for key in self.available_devices.keys():
                    if key in message.topic:
                        device = self.available_devices[key]
                        break  # Exit loop after first match
                if device is not None:
                    current_time = time.time()
                    state_vars = payload['data'][0].get('stateVariables', {})
                    with self._lock:
                        if device["device_type"] == DeviceConfig.GRID.name:
                            if 'emeter:power_aminus' in state_vars:
                                self.instant_power_injected = state_vars['emeter:power_aminus']
                                self.energy_injected.add_point(current_time, self.instant_power_injected)
                            if 'emeter:power_aplus' in state_vars:
                                self.instant_power_from_grid = state_vars['emeter:power_aplus']
                                self.energy_from_grid.add_point(current_time, self.instant_power_from_grid)
                        if device["device_type"] == DeviceConfig.PRODUCTION.name:
                            if 'emeter:power_aminus' in state_vars:
                                self.instant_power_produced = state_vars['emeter:power_aminus']
                                self.energy_produced.add_point(current_time, self.instant_power_produced)
                        if self.instant_power_produced is not None and self.instant_power_from_grid is not None and self.instant_power_injected is not None:
                            self.instant_power_consumed = self.instant_power_produced + self.instant_power_from_grid - self.instant_power_injected
                            self.energy_consumed.add_point(current_time, self.instant_power_consumed)
                    _LOGGER.debug(f'Power Produced: {self.instant_power_produced} Power From Grid: {self.instant_power_from_grid} Power To Grid: {self.instant_power_injected} Total Power Consumed: {self.instant_power_consumed}')
                    if self.hass:
                        asyncio.run_coroutine_threadsafe(
                            self.async_send_signal(), 
                            self.hass.loop
                        )
    def subscribeToTopics(self):        
            # Subscribe to all device topics
            for device in self.available_devices.values():            
                for topic_type in ["fromDev/realtime", "fromDev/module/changed"]:
                    topic = f'{device["type"]}/{device["deviceLocalId"]}/{topic_type}'
                    self._mqtt_client.subscribe(topic, 1, self.custom_callback)
                    self._mqtt_client.subscribe(topic, 0, self.custom_callback)

    async def periodic_task(self):
            while True:
                try:
                    #MQTT was disconnecting after about 1 day via web socket handshake failure
                    #refresh period is set to 20 hours, it will disconnect a reconnect to avoid drops
                    #and re-subscribe to topic
                    if self.mqttRefresh == self.mqqtRefreshPeriod:
                        _LOGGER.debug("Disconnecting MQTT")
                        await self.hass.async_add_executor_job(self._mqtt_client.disconnect)
                        _LOGGER.debug("Reconnecting MQTT")
                        await self.hass.async_add_executor_job(self._setup_mqtt)
                        await self.hass.async_add_executor_job(self.subscribeToTopics)
                        self.mqttRefresh = 0
                    # Activate real-time data for all devices
                    for device in self.available_devices.values():
                        activate_msg = {
                            "id": str(uuid.uuid4()),
                            "operationType": "realtime",
                            "messageType": "request",
                            "data": {"timeout": 60}
                        }
                        topic = f'{device["type"]}/{device["deviceLocalId"]}/toDev/realtime'
                        _LOGGER.debug("Republishing message request MQTT")
                        self._mqtt_client.publish(topic, json.dumps(activate_msg), 1)
                    self.mqttRefresh += 1
                except:
                    _LOGGER.critical("Error detected, disconnecting & reconnecting MQTT")
                    await self.hass.async_add_executor_job(self._mqtt_client.disconnect)
                    await self.hass.async_add_executor_job(self._setup_mqtt)
                    await self.hass.async_add_executor_job(self.subscribeToTopics)
                    self.mqttRefresh = 0
                    # Activate real-time data for all devices
                    for device in self.available_devices.values():
                        activate_msg = {
                            "id": str(uuid.uuid4()),
                            "operationType": "realtime",
                            "messageType": "request",
                            "data": {"timeout": 60}
                        }
                        topic = f'{device["type"]}/{device["deviceLocalId"]}/toDev/realtime'
                        _LOGGER.debug("Republishing message request MQTT")
                        self._mqtt_client.publish(topic, json.dumps(activate_msg), 1)
                await asyncio.sleep(60)

    async def async_authenticate_and_subscribe(self):
        """Main entrypoint: runs all blocking code in executor."""
        await self.hass.async_add_executor_job(self.auth)
        await self._async_retrieve_devices_and_modules()
        await self.hass.async_add_executor_job(self._setup_mqtt)
        self.hass.loop.create_task(self.periodic_cognito())
        self.hass.loop.create_task(self.periodic_task())

    async def _authenticate_and_subscribe(self):
        await self.async_authenticate_and_subscribe()

        self.subscribeToTopics()
        # Activate real-time data for all devices
        #self.hass.loop.create_task(self.periodic_task())
        
        # Keep MQTT running
        #while not self._stop_event.is_set():
        #    await asyncio.sleep(1)

    def get_values(self):
        """Thread-safe retrieval of all sensor values."""
        with self._lock:
            return {
                "instant_power_produced": self.instant_power_produced,
                "instant_power_consumed": self.instant_power_consumed,
                "instant_power_from_grid": self.instant_power_from_grid,
                "instant_power_injected": self.instant_power_injected,
                "available_device_ids": list(self.available_device_ids),
                "house_id": self.house_id,
                "user_id": self.user_id,
                "energy_consumed": self.energy_consumed.get_sum(),
                "energy_injected": self.energy_injected.get_sum(),
                "energy_from_grid": self.energy_from_grid.get_sum(),
                "energy_produced": self.energy_produced.get_sum()
            }