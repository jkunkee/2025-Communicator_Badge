
import uasyncio as aio  # type: ignore

from apps.base_app import BaseApp
from libs.micropython_scd30.scd30 import SCD30
from libs.sps30_micropython.sps30 import SPS30
from net.net import register_receiver, send, BROADCAST_ADDRESS
from net.protocols import Protocol, NetworkFrame
from ui.page import Page
import ui.styles as styles
import lvgl
import utime

# Yes, this is a Doctor Who reference
ATMOS_PROTOCOL = Protocol(port=25, name="AtmosphereData", structdef="!Bffffffff")
# v0: !Bfff - version, co2, temp, hum
# v1: !Bffffffff - add five particle count buckets
ATMOS_VERSION = 1

class AtmosphereData(BaseApp):
    """ This class either receives and displays atmosphere data (think air quality/AQI)
        or it uses attached I2C sensors to generate and display the same.

        It is currently inflexible with a very basic UI. Please feel free to make it better :)
    """

    def __init__(self, name: str, badge):
        super().__init__(name, badge)

        # device constants
        self.sensor_refresh_interval_ms = 5000
        scd30_address = 0x61
        sps30_address = 0x69

        # main app timing info
        self.foreground_sleep_ms = 10
        self.background_sleep_ms = self.sensor_refresh_interval_ms

        # See what's out there and set it up
        # n.b. this is usually naive and ignores i2c spec discovery mechanics
        i2c_scan_result = self.badge.sao_i2c.scan()

        if scd30_address in i2c_scan_result:
            self.scd30 = SCD30(self.badge.sao_i2c, scd30_address) # leave internal sleep at default 1000us
            self.scd30.set_measurement_interval(int(self.sensor_refresh_interval_ms/1000))
        else:
            self.scd30 = None

        if sps30_address in i2c_scan_result:
            self.sps30 = SPS30(self.badge.sao_i2c, sps30_address)
            self.sps30.start_measurement()
        else:
            self.sps30 = None

        # If we have a sensor, act as a data producer; otherwise, listen on LoRa
        self.producing_data = self.scd30 != None or self.sps30 != None

        # To decouple the timing of LoRa broadcasts, UI updates, and sensor reads,
        # each sensor reading is cached in these variables.
        # TODO: don't Fill out dummy data to avoid crashes when the sequencing isn't right, just get it right
        self.co2_measurement = [-1.0, -1.0, -1.0]
        self.particle_measurement = []
        for idx in range(0,4+5+2):
            self.particle_measurement.append(["",-1.0]) # incomplete dummy data

        # Data freshness indicator
        self.screen_has_latest_data = True
        # LoRa rate limiter (minimum broadcast interval is sensor_refresh_interval_ms)
        self.last_transmission = 0

        # UI object tracking (TODO: lean heavier on LVGL and micropython to avoid this)
        self.current_line_labels = []

    def start(self):
        super().start()
        if not self.producing_data:
            register_receiver(ATMOS_PROTOCOL, self.receive_message)

    def receive_message(self, message: NetworkFrame):
        """Handle incoming messages."""
        print(f"atmos received message {message.payload}") # A bit chatty, innit
        if message.port == ATMOS_PROTOCOL.port and message.payload[0] == ATMOS_VERSION:
            self.co2_measurement = message.payload[1:3]
            self.particle_measurement = message.payload[4:8]
            self.screen_has_latest_data = False

    def poll_data(self):
        if not self.producing_data:
            return
        # This scd30 driver isn't very resilient to the device falling off the bus sometimes,
        # but this is a wearable so we just deal with it.
        try:
            transmit_new_data = False
            now = utime.ticks_ms()
            if self.scd30 and self.scd30.get_status_ready():
                self.screen_has_latest_data = False
                transmit_new_data = True
                print(f"co2: {self.co2_measurement}")
                self.co2_measurement = self.scd30.read_measurement()
            if self.sps30 and self.sps30.read_data_ready():
                self.screen_has_latest_data = False
                transmit_new_data = True
                self.particle_measurement = self.sps30.read_measurement()
                print(f"part: {self.particle_measurement}")
            # Some sensors update frequently, so use holdoff to avoid spamming LoRa
            if transmit_new_data and (now - self.last_transmission) > self.sensor_refresh_interval_ms:
                tx_msg = NetworkFrame().set_fields(protocol=ATMOS_PROTOCOL,
                                                destination=BROADCAST_ADDRESS,
                                                payload=(
                                                    int(ATMOS_VERSION), # version
                                                    float(self.co2_measurement[0]), # ppm CO2
                                                    float(self.co2_measurement[1]), # deg C
                                                    float(self.co2_measurement[2]), # percent relative humidity
                                                    float(self.particle_measurement[4][1]), # particles/cm^3
                                                    float(self.particle_measurement[5][1]), # particles/cm^3
                                                    float(self.particle_measurement[6][1]), # particles/cm^3
                                                    float(self.particle_measurement[7][1]), # particles/cm^3
                                                    float(self.particle_measurement[8][1]), # particles/cm^3
                                                ))
                self.badge.lora.send(tx_msg)
                print("ATMOS transmitted")
                self.last_transmission = now
        except:
            print("scd30 read failure")

    def refresh_labels(self) -> None:
        if self.screen_has_latest_data:
            return
        else:
            self.screen_has_latest_data = True

        text_to_display = []

        if self.producing_data and not self.scd30:
            text_to_display.append("SCD30 CO2 sensor not present")
            text_to_display.append("")
            text_to_display.append("")
        else:
            text_to_display.append(f"{self.co2_measurement[0]:.0f} ppm CO2")
            text_to_display.append(f"{self.co2_measurement[1]:.2f} deg C ({(self.co2_measurement[1] * 9 / 5) + 32:.0f} deg F)")
            text_to_display.append(f"{self.co2_measurement[2]}% rh")

        # unused rows
        text_to_display.append("")
        text_to_display.append("")
        text_to_display.append("")
        text_to_display.append("")

        if self.producing_data and not self.sps30:
            text_to_display.append("SCD30 CO2 sensor not present")
            text_to_display.append("")
            text_to_display.append("")
        else:
            text_to_display.append(f"{self.particle_measurement[4][1]} {self.particle_measurement[4][0]} particles/cm^3")
            text_to_display.append(f"{self.particle_measurement[5][1]} {self.particle_measurement[5][0]} particles/cm^3")
            text_to_display.append(f"{self.particle_measurement[6][1]} {self.particle_measurement[6][0]} particles/cm^3")
            text_to_display.append(f"{self.particle_measurement[7][1]} {self.particle_measurement[7][0]} particles/cm^3")
            text_to_display.append(f"{self.particle_measurement[8][1]} {self.particle_measurement[8][0]} particles/cm^3")

        # unused rows
        text_to_display.append("")
        text_to_display.append("")

        for idx, text in enumerate(text_to_display):
            if idx < len(self.current_line_labels):
                self.current_line_labels[idx].set_text(text)
            else:
                print("airquality: line skipped because screen small and not scrolling")

    def run_foreground(self):
        self.poll_data() # Does nothing if no sensors present
        self.refresh_labels() # Does nothing if data is not new

        if self.badge.keyboard.f1():
            pass
        if self.badge.keyboard.f2():
            pass
        if self.badge.keyboard.f3():
            pass
        if self.badge.keyboard.f4():
            pass
        ## Co-op multitasking: all you have to do is get out
        if self.badge.keyboard.f5():
            self.badge.display.clear()
            self.switch_to_background()

    def run_background(self):
        super().run_background()
        self.poll_data() # Does nothing if no sensors present

    def switch_to_foreground(self):
        super().switch_to_foreground()
        self.p = Page()
        ## Note this order is important: it renders top to bottom that the "content" section expands to fill empty space
        ## If you want to go fully clean-slate, you can draw straight onto the p.scr object, which should fit the full screen.
        self.p.create_infobar(["Atmospheric Data Display", ""])
        if not self.producing_data:
            self.p.infobar_right.set_text("Awaiting packets")
        else:
            self.p.infobar_right.set_text(f"Polling sensors every ~{int(self.sensor_refresh_interval_ms/1000)}s")
        self.p.create_content()
        self.current_line_labels = []
        # Two columns of seven rows each, addressed in a flat array
        y_pos = 0
        for _ in range(0, 7):
            label = lvgl.label(self.p.content)
            label.set_pos(25, y_pos)
            self.current_line_labels.append(label)
            y_pos += 13
        y_pos = 0
        for _ in range(0, 7):
            label = lvgl.label(self.p.content)
            label.set_pos(214, y_pos)
            self.current_line_labels.append(label)
            y_pos += 13
        self.p.create_menubar(["", "", "", "", "Done"])
        self.p.replace_screen()

        self.screen_has_latest_data = False
        self.refresh_labels()

    def switch_to_background(self):
        # TODO: If the LVGL objects are properly parented, this loop may not be necessary.
        self.current_line_labels = []
        self.p = None
        super().switch_to_background()

# Zampire App Manager metadata
APP_NAME = "AirQuality"
APP_CLASS = AtmosphereData
