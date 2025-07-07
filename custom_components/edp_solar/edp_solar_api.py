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
from .devices_enum import DeviceConfig
from .const import REGION, USER_POOL_ID, ID_USER_POOL, CLIENT_ID, CLIENT_SECRET, IDENTITY_POOL_ID, IOT_HOST
from riemann_sum import TrapezoidalRiemannSumMulti
import time

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
        # Calculated variables
        self._riemann = TrapezoidalRiemannSumMulti()
        self._riemann_sums = {}

        self._stop_event = threading.Event()
        self._mqtt_thread = None

        self.mqttRefresh = 0
        self.mqqtRefreshPeriod = 20

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
    
    def get_riemann_sums(self):
    with self._lock:
        return dict(self._riemann_sums)

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
            self._authenticate_and_subscribe()
        except Exception as ex:
            _LOGGER.error("EDP Solar API thread crashed: %s", ex, exc_info=True)

    async def async_send_signal(self):
        try:
            from homeassistant.helpers.dispatcher import async_dispatcher_send
            async_dispatcher_send(self.hass, "edp_solar_update")
        except Exception as e:
            _LOGGER.error("Signal dispatch failed: %s", e)

    def _authenticate_and_subscribe(self):
        # --- AWS Cognito and API constants ---

        # --- Helper functions ---
        def get_secret_hash(username, client_id, client_secret):
            message = username + client_id
            dig = hmac.new(
                client_secret.encode('utf-8'),
                msg=message.encode('utf-8'),
                digestmod=hashlib.sha256
            ).digest()
            return base64.b64encode(dig).decode()

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
            cognito_identity = boto3.client('cognito-identity', region_name=REGION)

            secret_hash = get_secret_hash(self.username, CLIENT_ID, CLIENT_SECRET)
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

            device_secret_verifier = generate_device_secret_verifier(
                self.username, self.device_key, self.device_group_key, generate_random_device_password(),
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
            secret_hash = get_secret_hash(self.user_id, CLIENT_ID, CLIENT_SECRET)
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
            device_secret_verifier = generate_device_secret_verifier(
                self.user_id, self.device_key, self.device_group_key, generate_random_device_password(),
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

        auth(self)

        async def periodic_cognito():
            while True:
                print("Async task executed!")
                await asyncio.sleep(3600)  # 10 seconds interval
                auth(self)
        self.hass.loop.create_task(periodic_cognito())
        
        _LOGGER.debug("Retrieving Houses")

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
        response = requests.get(url, headers=headers)
        house = response.json()
        house_id = house["houses"][0]["houseId"]

        # Step 5: Get Devices and Modules
        _LOGGER.debug("Retrieving Devices")
        url = f'https://uiapi.emcp.edp.com/equipment/houses/{house_id}/device'
        devices_response = requests.get(url, headers=headers)
        devices = devices_response.json()

        device_ids = [device["deviceLocalId"] for device in devices]
        #device_deviceId[device["deviceId"]: device for device in devices]

        url = f'https://uiapi.emcp.edp.com/equipment/houses/{house_id}/modules'
        modules_response = requests.get(url, headers=headers)
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

        _LOGGER.debug("Starting MQTT")
        # Step 6: Setup MQTT
        current_dir = os.path.dirname(os.path.abspath(__file__))
        ca_path = os.path.join(current_dir, 'certificates/AmazonRootCA1.pem')
        
        def custom_disconnect_callback(client, userdata, rc):
            print(f"Disconnected from AWS IoT Core with result code: {rc}")
            if rc != 0:
                print("Unexpected disconnect. Attempting to reconnect...")
                self._mqtt_client.configureIAMCredentials(self.access_key, self.secret_key, self.session_token)
                self._mqtt_client.connect()

        mqtt_client = AWSIoTMQTTClient(str(uuid.uuid4()), useWebsocket=True)
        mqtt_client.configureEndpoint(IOT_HOST, 443)
        mqtt_client.configureCredentials(ca_path)
        mqtt_client.configureIAMCredentials(self.access_key, self.secret_key, self.session_token)
        mqtt_client.connect()
        self._mqtt_client = mqtt_client

        def custom_callback(client, userdata, message):
            payload = json.loads(message.payload.decode())
            if message.topic.endswith("/fromDev/realtime") and 'data' in payload and len(payload['data']) > 0:
                device = None
                for key in self.available_devices.keys():
                    if key in message.topic:
                        device = self.available_devices[key]
                        break  # Exit loop after first match
                if device is not None:
                    state_vars = payload['data'][0].get('stateVariables', {})
                    now_ts = time.time()
                    with self._lock:
                        if device["device_type"] == DeviceConfig.GRID.name:
                            if 'emeter:power_aminus' in state_vars:
                                self.instant_power_injected = state_vars['emeter:power_aminus']
                                val = getattr(self, "instant_power_injected", None)
                                sum_val = self._riemann.add_point("instant_power_injected", now_ts, val)
                                self._riemann_sums["instant_power_injected"] = sum_val
                            if 'emeter:power_aplus' in state_vars:
                                self.instant_power_from_grid = state_vars['emeter:power_aplus']
                                val = getattr(self, "instant_power_from_grid", None)
                                sum_val = self._riemann.add_point("instant_power_from_grid", now_ts, val)
                                self._riemann_sums["instant_power_from_grid"] = sum_val
                        if device["device_type"] == DeviceConfig.PRODUCTION.name:
                            if 'emeter:power_aminus' in state_vars:
                                self.instant_power_produced = state_vars['emeter:power_aminus']
                                val = getattr(self, "instant_power_produced", None)
                                sum_val = self._riemann.add_point("instant_power_produced", now_ts, val)
                                self._riemann_sums["instant_power_produced"] = sum_val
                        if self.instant_power_produced is not None and self.instant_power_from_grid is not None and self.instant_power_injected is not None:
                            self.instant_power_consumed = self.instant_power_produced + self.instant_power_from_grid - self.instant_power_injected
                            val = getattr(self, "instant_power_consumed", None)
                            sum_val = self._riemann.add_point("instant_power_consumed", now_ts, val)
                            self._riemann_sums["instant_power_consumed"] = sum_val
                    _LOGGER.debug(f'S: {self.instant_power_produced} G: {self.instant_power_from_grid} T: {self.instant_power_consumed}')
                    
                    if self.hass:
                        asyncio.run_coroutine_threadsafe(
                            self.async_send_signal(), 
                            self.hass.loop
                        )
        async def periodic_task():
            while True:
                print("Async task executed!")
                if self.mqttRefresh == self.mqqtRefreshPeriod:
                    self._mqtt_client.disconnect()
                    self._mqtt_client.configureIAMCredentials(self.access_key, self.secret_key, self.session_token)
                    self._mqtt_client.connect()
                    subscribeToTopics(self)
                    self.mqttRefresh = 0
                # Activate real-time data for all devices
                for device in self.available_devices.values():
                    activate_msg = {
                        "id": str(uuid.uuid4()),
                        "operationType": "realtime",
                        "messageType": "request",
                        "data": {"timeout": 3600}
                    }
                    topic = f'{device["type"]}/{device["deviceLocalId"]}/toDev/realtime'
                    self._mqtt_client.publish(topic, json.dumps(activate_msg), 1)
                self.mqttRefresh += 1
                await asyncio.sleep(3600)  # 10 seconds interval
                
        def subscribeToTopics(self):        
            # Subscribe to all device topics        
            _LOGGER.critical("aqui")
            for device in self.available_devices.values():            
                for topic_type in ["fromDev/realtime", "fromDev/module/changed"]:
                    topic = f'{device["type"]}/{device["deviceLocalId"]}/{topic_type}'
                    self._mqtt_client.subscribe(topic, 1, custom_callback)
                    self._mqtt_client.subscribe(topic, 0, custom_callback)
        subscribeToTopics(self)
        self.hass.loop.create_task(periodic_task())
        
        # Keep MQTT running
        while not self._stop_event.is_set():
            time.sleep(1)

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
                "energy_produced": self.get_riemann_sums().get("instant_power_produced"),
                "energy_consumed": self.get_riemann_sums().get("instant_power_consumed"),
                "energy_from_grid": self.get_riemann_sums().get("instant_power_from_grid"),
                "energy_injected": self.get_riemann_sums().get("instant_power_injected"),
            }
