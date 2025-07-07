from homeassistant import config_entries
import voluptuous as vol
from .const import DOMAIN

class EdpSolarConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for EDP Solar."""

    VERSION = 1

    async def async_step_user(self, user_input=None):
        errors = {}
        if user_input is not None:
            # Here you could add authentication logic, e.g., test credentials
            # For basic storage, just create the entry
            return self.async_create_entry(
                title=user_input["username"],
                data={
                    "username": user_input["username"],
                    "password": user_input["password"]
                }
            )

        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema({
                vol.Required("username"): str,
                vol.Required("password"): str
            }),
            errors=errors
        )
