# Edp Solar Integration

Integration of EDP Solar Features with Home Assistant, focused in obtaining live data from the EDP Solar sensors.

## What is it?

### From the supplier site
The EDP Solar App is an exclusive service from EDP Comercial that allows you to monitor your energy consumption and solar energy production, as well as the use of the energy you produce. In addition, the EDP Solar App analyses your consumption pattern, checking that your tariff and power are optimized, alerts you if you run out of power or internet at home and warns you if your solar production system fails.

### What the integration does
Integration captures the information provided by the sensors already installed in your home and makes them available in Home Assistant as Sensors.
The integration is done to support my needs in Home Assistant and is not in any way affilliated with the supplier.

### What the integration provides

Integration creates a Device called **EDP Solar** with several available sensor entities.

#### Currently Available Sensors

| Name | Sensor Name | Value Origin | Units | Value Meaning |
| :--- | :---: | :---: | :---: | :--- |
| Instant Power Produced | sensor.edp_solar_instant_power_produced | Direct Read | W | Instant Power (Watts) Produced by the Solar Panel |
| Instant Power Injected | sensor.edp_solar_instant_power_injected | Direct Read | W | Instant Power (Watts) Injected to the Grid from produced power |
| Instant Power From Grid | sensor.edp_solar_instant_power_from_grid | Direct Read | W | Instant Power (Watts) Consumed From the Grid to cover consumption over production |
| Instant Power Consumed | sensor.edp_solar_instant_power_consumed | Calculated | W | Calculated by summing Instant Power Produced and From Grid and subtracting Injected |
| Energy Produced | sensor.edp_solar_energy_produced | Calculated | Wh | Energy (watt-hour) Produced by the Solar Panel, calculated via Trapzoidal Riemann Sum |
| Energy Injected | sensor.edp_solar_energy_injected | Calculated | Wh | Energy (watt-hour) Injected to the Grid from produced power, calculated via Trapzoidal Riemann Sum  |
| Energy From Grid | sensor.edp_solar_energy_from_grid | Calculated | Wh | Energy (watt-hour) Consumed From the Grid, calculated via Trapzoidal Riemann Sum  |
| Energy Consumed | sensor.edp_solar_energy_consumed | Calculated | Wh | Energy (watt-hour) Consumed, calculated via Trapzoidal Riemann Sum  |
| House Id | sensor.edp_solar_house_id | N.A. | N.A. | House Id in EDP Solar system, used to retrive information, automatically retrieved from requests |
| User Id | sensor.edp_solar_user_id | N.A. | N.A. | User Id in EDP Solar system, used to retrive information, automatically retrieved from requests |
| Device Ids | sensor.edp_solar_available_device_ids | N.A. | N.A. | list of Ids of the avaible devices within your EDP Solar setup, used to retrive information, automatically retrieved from requests |
