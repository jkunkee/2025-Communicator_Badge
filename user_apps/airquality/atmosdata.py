
import uasyncio as aio  # type: ignore

from apps.base_app import BaseApp
from libs.micropython_scd30.scd30 import SCD30
from libs.sps30_micropython.sps30 import SPS30
from net.net import register_receiver, send, BROADCAST_ADDRESS
from net.protocols import Protocol, NetworkFrame
from ui.page import Page, SCREEN_HEIGHT, SCREEN_WIDTH, MENU_HEIGHT, INFOBAR_HEIGHT
import ui.styles as styles
import lvgl
import random
import utime
from collections import deque

# Yes, this is a Doctor Who reference
ATMOS_PROTOCOL = Protocol(port=25, name="AtmosphereData", structdef="!Bffffffff")
# v0: !Bfff - version, co2, temp, hum
# v1: !Bffffffff - add five particle count buckets
ATMOS_VERSION = 1

class AtmosphereData(BaseApp):
    """ This class either receives and displays atmosphere data (think air quality/AQI)
        or it uses attached I2C sensors to generate and display the same.

        There are two state machines here:
        A. Sensor reading
        B. UI refresh

        Sensors get polled during both background and foreground execution as fast as per-sensor 'ready' status indicates.
        A delay timer of sorts waits to broadcast current data every so often, while
        the UI refresh updates the foregrounded UI elements as fast as data is available.

        The UI has a state variable that is updated by foreground keypresses.
        This, in turn, drives which Page is currently constructed and foregrounded.

        N.B. Page.replace_screen tells LVGL to delete the current Page.
    """

    def __init__(self, name: str, badge):
        super().__init__(name, badge)

        # global configuration constants
        self.spoof_data_prod = False
        self.last_data_spoof = 0

        # To decouple the timing of LoRa broadcasts, UI updates, and sensor reads,
        # each sensor reading is cached in these variables.
        self.series_len = 60
        self.series_map = {
            "co2_ppm": deque([], self.series_len),
            "temp_C": deque([], self.series_len),
            "hum_%": deque([], self.series_len),
            "part_0.5umppcm3": deque([], self.series_len),
            "part_1.0umppcm3": deque([], self.series_len),
            "part_2.5umppcm3": deque([], self.series_len),
            "part_4.0umppcm3": deque([], self.series_len),
            "part_10.0umppcm3": deque([], self.series_len),
        }
        self.series_freshness_map = {
            "scd30": False,
            "sps30": False,
        }

        # device constants
        self.max_read_interval_ms = 5000
        scd30_address = 0x61
        scd30_device_update_interval_s = 5
        sps30_address = 0x69

        # network constants
        self.broadcast_interval = 5000

        # app parameters
        self.foreground_sleep_ms = 10
        self.background_sleep_ms = self.max_read_interval_ms

        # See what's out there and set it up
        # n.b. this is usually naive and ignores i2c spec discovery mechanics
        i2c_scan_result = self.badge.sao_i2c.scan()

        if scd30_address in i2c_scan_result:
            self.scd30 = SCD30(self.badge.sao_i2c, scd30_address) # leave internal sleep at default 1000us
            self.scd30.set_measurement_interval(scd30_device_update_interval_s)
        else:
            self.scd30 = None

        if sps30_address in i2c_scan_result:
            self.sps30 = SPS30(self.badge.sao_i2c, sps30_address)
            self.sps30.start_measurement()
        else:
            self.sps30 = None

        # If we have a sensor, act as a data producer; otherwise, listen on LoRa
        self.producing_data = self.scd30 != None or self.sps30 != None or self.spoof_data_prod

        # LoRa rate limiter (minimum broadcast interval is sensor_refresh_interval_ms)
        self.last_transmission = 0

        # UI object tracking
        self.co2_page = None
        self.co2_chart = None
        self.co2_textarea = None
        self.part_page = None
        self.part_chart = None
        self.part_textarea = None

        # UI state machine
        self.UI_STATES = ["CO2", "Particulate"]
        self.ui_state = self.UI_STATES[0]

    def start(self):
        super().start()
        if not self.producing_data:
            register_receiver(ATMOS_PROTOCOL, self.receive_message)

    def receive_message(self, message: NetworkFrame):
        """Handle incoming messages."""
        print(f"atmos received message {message.payload}") # A bit chatty, innit
        if message.port == ATMOS_PROTOCOL.port and message.payload[0] == ATMOS_VERSION:
            self.series_map["co2_ppm"].append(message.payload[1])
            self.series_map["temp_C"].append(message.payload[2])
            self.series_map["hum_%"].append(message.payload[3])
            self.series_freshness_map["scd30"] = True
            self.series_map["part_0.5umppcm3"].append(message.payload[4])
            self.series_map["part_1.0umppcm3"].append(message.payload[5])
            self.series_map["part_2.5umppcm3"].append(message.payload[6])
            self.series_map["part_4.0umppcm3"].append(message.payload[7])
            self.series_map["part_10.0umppcm3"].append(message.payload[8])
            self.series_freshness_map["sps30"] = True

    def poll_data(self):
        if not self.producing_data:
            return
        now = utime.ticks_ms()
        if self.spoof_data_prod:
            if (now - self.last_data_spoof) > self.max_read_interval_ms:
                self.series_map["co2_ppm"].append(random.random()*600 + 400)
                self.series_map["temp_C"].append(random.random()*35.0)
                self.series_map["hum_%"].append(random.random()*100.0)
                self.series_freshness_map["scd30"] = True
                self.series_map["part_0.5umppcm3"].append(random.random()*50.0)
                self.series_map["part_1.0umppcm3"].append(random.random()*50.0)
                self.series_map["part_2.5umppcm3"].append(random.random()*50.0)
                self.series_map["part_4.0umppcm3"].append(random.random()*50.0)
                self.series_map["part_10.0umppcm3"].append(random.random()*50.0)
                self.series_freshness_map["sps30"] = True
                self.last_data_spoof = now
            return
        # This scd30 driver isn't very resilient to the device falling off the bus sometimes,
        # but this is a wearable so we just deal with it.
        try:
            if self.scd30 and self.scd30.get_status_ready():
                co2_measurement = self.scd30.read_measurement()
                print(f"co2: {co2_measurement}")
                self.series_map["co2_ppm"].append(float(co2_measurement[0]))
                self.series_map["temp_C"].append(float(co2_measurement[1]))
                self.series_map["hum_%"].append(float(co2_measurement[2]))
                self.series_freshness_map["scd30"] = True
        except Exception as e:
            print("scd30 read failure")
            print(e)
        try:
            if self.sps30 and self.sps30.read_data_ready():
                particle_measurement = self.sps30.read_measurement()
                print(f"part: {particle_measurement}")
                self.series_map["part_0.5umppcm3"].append(particle_measurement[4][1])
                self.series_map["part_1.0umppcm3"].append(particle_measurement[5][1])
                self.series_map["part_2.5umppcm3"].append(particle_measurement[6][1])
                self.series_map["part_4.0umppcm3"].append(particle_measurement[7][1])
                self.series_map["part_10.0umppcm3"].append(particle_measurement[8][1])
                self.series_freshness_map["sps30"] = True
        except Exception as e:
            print("sps30 read failure")
            print(e)

    def is_all_data_fresh(self) -> bool:
        return self.series_freshness_map["scd30"] and self.series_freshness_map["sps30"]

    def is_any_data_fresh(self) -> bool:
        return self.series_freshness_map["scd30"] or self.series_freshness_map["sps30"]

    def reset_freshness(self) -> None:
        self.series_freshness_map["scd30"] = False
        self.series_freshness_map["sps30"] = False

    def transmit_on_interval(self) -> None:
        if not self.producing_data:
            return
        now = utime.ticks_ms()
        # Some sensors update frequently, so use holdoff to avoid spamming LoRa
        if (now - self.last_transmission) > self.broadcast_interval:
            tx_msg = NetworkFrame().set_fields(protocol=ATMOS_PROTOCOL,
                                            destination=BROADCAST_ADDRESS,
                                            payload=(
                                                int(ATMOS_VERSION), # version
                                                float(self.series_map["co2_ppm"][-1]), # ppm CO2
                                                float(self.series_map["temp_C"][-1]), # deg C
                                                float(self.series_map["hum_%"][-1]), # percent relative humidity
                                                float(self.series_map["part_0.5umppcm3"][-1]), # particles/cm^3
                                                float(self.series_map["part_1.0umppcm3"][-1]), # particles/cm^3
                                                float(self.series_map["part_2.5umppcm3"][-1]), # particles/cm^3
                                                float(self.series_map["part_4.0umppcm3"][-1]), # particles/cm^3
                                                float(self.series_map["part_10.0umppcm3"][-1]), # particles/cm^3
                                            ))
            self.badge.lora.send(tx_msg)
            print("ATMOS transmitted")
            self.last_transmission = now

    def refresh_screens(self) -> None:
        return



        if self.screen_has_latest_data:
            return
        else:
            self.screen_has_latest_data = True

        text_to_display = []

        if self.producing_data and not self.scd30 and not self.spoof_data_prod:
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

        if self.producing_data and not self.sps30 and not self.spoof_data_prod:
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

        self.chart.set_next_value(self.co2_series, int(self.co2_measurement[0]))
        self.chart.set_next_value(self.hum_series, int(self.co2_measurement[2]))

    def load_current_screen(self):
        # TODO: UI_STATES is a bit clunky
        if self.ui_state == "CO2":
            lvgl.screen_load(self.co2_page.scr)
        elif self.ui_state == "Particulate":
            lvgl.screen_load(self.part_page.scr)
        else:
            pass

    def run_foreground(self):
        self.poll_data() # Does nothing if no sensors present

        if self.is_any_data_fresh():
            if self.is_all_data_fresh():
                self.transmit_on_interval()
            self.refresh_screens() # Does nothing if data is not new
            self.reset_freshness()

        cur_ui_state = self.ui_state

        if self.badge.keyboard.f1():
            cur_index = self.UI_STATES.index(self.ui_state)
            new_index = (cur_index - 1) % len(self.UI_STATES)
            self.ui_state = self.UI_STATES[new_index]
            print(f"ATMOS new state: {self.ui_state}")
        if self.badge.keyboard.f2():
            cur_index = self.UI_STATES.index(self.ui_state)
            new_index = (cur_index + 1) % len(self.UI_STATES)
            self.ui_state = self.UI_STATES[new_index]
            print(f"ATMOS new state: {self.ui_state}")
        if self.badge.keyboard.f3():
            pass
        if self.badge.keyboard.f4():
            pass
        ## Co-op multitasking: all you have to do is get out
        if self.badge.keyboard.f5():
            self.badge.display.clear()
            self.switch_to_background()
            return

        if cur_ui_state != self.ui_state:
            self.load_current_screen()

    def run_background(self):
        super().run_background()
        self.poll_data() # Does nothing if no sensors present
        if (self.is_all_data_fresh()):
            self.transmit_on_interval()
            self.reset_freshness()

    def switch_to_foreground(self):
        super().switch_to_foreground()

        self.co2_page = Page()
        self.co2_page.create_infobar(["Atmospheric Data Display", "SCD30 (NDIR CO2)"])
        self.co2_page.create_content()
        self.co2_textarea = lvgl.textarea(self.co2_page.content)
        self.co2_textarea.set_width(lvgl.pct(50))
        self.co2_textarea.set_height(lvgl.pct(100))
        self.co2_chart = lvgl.chart(self.co2_page.content)
        self.co2_chart.set_width(lvgl.pct(50))
        self.co2_chart.set_height(lvgl.pct(100))
        self.co2_page.create_menubar(["Prev", "Next", "", "", "Home"])

        self.part_page = Page()
        self.part_page.create_infobar(["Atmospheric Data Display", "SPS30 (particulate)"])
        self.part_page.create_content()
        self.part_textarea = lvgl.textarea(self.part_page.content)
        self.part_textarea.set_width(lvgl.pct(50))
        self.part_chart = lvgl.chart(self.part_page.content)
        self.part_chart.set_width(lvgl.pct(50))
        self.part_page.create_menubar(["Prev", "Next", "", "", "Home"])

        self.refresh_screens()
        self.load_current_screen()

        return




        self.p.create_infobar(["Atmospheric Data Display", ""])
        if not self.producing_data:
            self.p.infobar_right.set_text("Awaiting packets")
        elif self.spoof_data_prod:
            self.p.infobar_right.set_text("Spoofing data")
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
        self.p.create_menubar(["Prev", "Next", "", "", "Home"])

        # chart page
        self.chart_page = Page()
        self.chart_page.create_infobar(["Atmospheric Data Display", self.p.infobar_right.get_text()])
        self.chart_page.create_content()
        self.chart = lvgl.chart(self.chart_page.content)
        self.chart.set_height(lvgl.pct(100))
        self.chart.set_width(lvgl.pct(100))
        self.chart.set_type(lvgl.chart.TYPE.LINE)
        self.chart.set_update_mode(lvgl.chart.UPDATE_MODE.SHIFT)
        self.chart.set_point_count(60)
        self.chart.set_axis_range(lvgl.chart.AXIS.PRIMARY_Y, 400, 2000)
        self.chart.set_axis_range(lvgl.chart.AXIS.SECONDARY_Y, 0, 100)
        self.co2_series = self.chart.add_series(lvgl.palette_main(lvgl.PALETTE.RED), lvgl.chart.AXIS.PRIMARY_Y)
        self.hum_series = self.chart.add_series(lvgl.palette_main(lvgl.PALETTE.BLUE), lvgl.chart.AXIS.SECONDARY_Y)
        self.chart_page.create_menubar(["Prev", "Next", "", "", "Home"])

        self.screen_has_latest_data = False
        self.refresh_screen()

        self.load_screen()

    def switch_to_background(self):
        self.current_line_labels = []
        # I'm doing my own LVGL management, so give Display.clear() (the standard to-background operation) something to
        # work with.
        temp_page = Page()
        lvgl.screen_load(temp_page.scr)

        self.co2_page.delete()
        self.co2_page = None
        self.co2_chart = None
        self.co2_textarea = None

        self.part_page.delete()
        self.part_page = None
        self.part_chart = None
        self.part_textarea = None

        super().switch_to_background()

# Zampire App Manager metadata
APP_NAME = "ATMOS"
APP_CLASS = AtmosphereData
