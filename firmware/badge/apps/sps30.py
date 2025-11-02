from machine import I2C
import struct
import time

class SPS30:
    def __init__(self, i2c: I2C, address: int = 0x69):
        self.i2c = i2c
        self.addr = address

    def _crc(self, data):
        crc = 0xFF
        for byte in data:
            crc ^= byte
            for _ in range(8):
                if crc & 0x80:
                    crc = (crc << 1) ^ 0x31
                else:
                    crc <<= 1
            crc &= 0xFF
        return crc

    def _send_command(self, cmd, args=b''):
        buf = bytearray()
        buf.append(cmd >> 8)
        buf.append(cmd & 0xFF)
        for i in range(0, len(args), 2):
            word = args[i:i+2]
            buf.extend(word)
            buf.append(self._crc(word))
        self.i2c.writeto(self.addr, buf)

    def start_measurement(self):
        self._send_command(0x0010, b'\x03\x00')  # start with float format
        time.sleep(0.1)

    def stop_measurement(self):
        self._send_command(0x0104)
        time.sleep(0.1)

    def read_data_ready(self):
        self._send_command(0x0202)
        time.sleep(0.005)
        raw = self.i2c.readfrom(self.addr, 3)
        if self._crc(raw[:2]) != raw[2]:
            raise Exception("CRC mismatch on data ready read")
        return (raw[0] << 8) | raw[1]
    
    def categorize_aqi_pm25(self, pm25_value):
        if pm25_value < 50:
            return "Good"
        elif 50 <= pm25_value <= 100:
            return "Moderate"
        elif 101 <= pm25_value <= 150:
            return "Unhealthy for Sensitive Groups"
        elif 151 <= pm25_value <= 200:
            return "Unhealthy"
        elif 201 <= pm25_value <= 300:
            return "Very Unhealthy"
        else:  # pm25_value > 300
            return "Hazardous"

    def read_measurement(self):
        self._send_command(0x0300)
        time.sleep(0.05)
        raw = self.i2c.readfrom(self.addr, 60)
        values = []
        aqi_pm25 = ""
        for i in range(0, 60, 6):
            chunk = raw[i:i+6]
            if self._crc(chunk[:2]) != chunk[2] or self._crc(chunk[3:5]) != chunk[5]:
                raise Exception("CRC mismatch in measurement block")
            # Unpack the 4 bytes of each measurement into a single float
            values.append(struct.unpack('>f', chunk[0:4])[0])
        
        # Get AQI category based on PM2.5 value
        aqi_pm25 = self.categorize_aqi_pm25(values[1])
        
        # Return measurements and AQI status
        return [
            ('mc_1.0', values[0]),
            ('mc_2.5', values[1]),
            ('mc_4.0', values[2]),
            ('mc_10.0', values[3]),
            ('nc_0.5', values[4]),
            ('nc_1.0', values[5]),
            ('nc_2.5', values[6]),
            ('nc_4.0', values[7]),
            ('nc_10.0', values[8]),
            ('typical_particle_size', values[9]),
            ('AQI', aqi_pm25)  # Add AQI to the returned data
        ]

