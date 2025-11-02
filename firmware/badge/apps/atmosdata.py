
import uasyncio as aio  # type: ignore

from apps.base_app import BaseApp
from apps import scd30
from net.net import register_receiver, send, BROADCAST_ADDRESS
from net.protocols import Protocol, NetworkFrame
from ui.page import Page
import ui.styles as styles
import lvgl

# Yes, this is a Doctor Who reference
ATMOS_PROTOCOL = Protocol(port=25, name="AtmosphereData", structdef="!Bfff")

class AtmosphereData(BaseApp):
    """ This class either receives and displays atmosphere data (think air quality/AQI)
        or it uses attached I2C sensors to generate and display the same.

        It is currently inflexible with a very basic UI. Please feel free to make it better :)
    """

    def __init__(self, name: str, badge):
        super().__init__(name, badge)

        self.sensor_refresh_interval_ms = 5000
        self.scd30_address = 0x61
        self.ATMOS_VERSION = 0

        self.foreground_sleep_ms = 10
        self.background_sleep_ms = self.sensor_refresh_interval_ms

        if 0x61 in self.badge.sao_i2c.scan():
            self.scd30 = scd30.SCD30(self.badge.sao_i2c, 0x61) # leave internal sleep at default 1000us
            self.scd30.set_measurement_interval(int(self.sensor_refresh_interval_ms/1000))
            self.producing_data = True
        else:
            self.scd30 = None
            self.producing_data = False

        self.measurement = [-1, -1, -1]
        self.screen_has_latest_data = True

        self.current_line_labels = []

    def start(self):
        super().start()
        register_receiver(ATMOS_PROTOCOL, self.receive_message)

    def receive_message(self, message: NetworkFrame):
        """Handle incoming messages."""
        print(f"atmos received message {message.payload}")
        # TODO do this check for register_receiver instead (this is easier to debug)
        if not self.producing_data and message.port == ATMOS_PROTOCOL.port and message.payload[0] == self.ATMOS_VERSION:
            self.measurement = message.payload[1:3]
            self.screen_has_latest_data = False

    def poll_data(self):
        # This scd30 driver isn't very resilient to the device falling off the bus sometimes,
        # but this is a wearable so we just deal with it.
        try:
            if self.scd30 and self.scd30.get_status_ready():
                self.screen_has_latest_data = False
                print(self.measurement)
                self.measurement = self.scd30.read_measurement()
                tx_msg = NetworkFrame().set_fields(protocol=ATMOS_PROTOCOL,
                                                destination=BROADCAST_ADDRESS,
                                                payload=(
                                                    int(self.ATMOS_VERSION), # version
                                                    float(self.measurement[0]), # ppm CO2
                                                    float(self.measurement[1]), # deg C
                                                    float(self.measurement[2]), # percent relative humidity
                                                ))
                self.badge.lora.send(tx_msg)
        except:
            print("scd30 read failure")

    def compose_lines(self) -> list[str]:
        l = []
        l.append(f"{self.measurement[0]:.0f} ppm CO2")
        l.append(f"{self.measurement[1]:.2f} deg C ({(self.measurement[1] * 9 / 5) + 32:.0f} deg F)")
        l.append(f"{self.measurement[2]}% rh")
        return l

    def refresh_labels(self) -> None:
        # I should be able to get LVGL to do vertical stacking for me
        # Many thanks to hwmon for showing how to do some of this
        y_pos = 18
        for label in self.current_line_labels:
            label.delete()
        self.current_line_labels = []
        text_to_display = self.compose_lines()
        for text in text_to_display:
            label = lvgl.label(self.badge.display.screen)
            self.current_line_labels.append(label)
            label.set_text(text)
            label.set_pos(25, y_pos)
            y_pos += 13

    def run_foreground(self):
        if self.producing_data:
            self.poll_data()
        else:
            pass # Wait for lora packets

        if not self.screen_has_latest_data:
            self.screen_has_latest_data = True
            self.refresh_labels()

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
        if self.producing_data:
            self.poll_data()
        else:
            pass # Wait for lora packets

    def switch_to_foreground(self):
        super().switch_to_foreground()
        self.p = Page()
        ## Note this order is important: it renders top to bottom that the "content" section expands to fill empty space
        ## If you want to go fully clean-slate, you can draw straight onto the p.scr object, which should fit the full screen.
        self.p.create_infobar(["Atmospheric Data Display", ""])
        self.p.create_content()
        self.p.create_menubar(["", "", "", "", "Done"])
        self.p.replace_screen()
        if not self.producing_data:
            self.p.infobar_right.set_text("Awaiting packets")
        else:
            self.p.infobar_right.set_text(f"Polling sensors every ~{int(self.sensor_refresh_interval_ms/1000)}s")
        self.screen_has_latest_data = False

    def switch_to_background(self):
        # TODO: If the LVGL objects are properly parented, this loop may not be necessary.
        self.current_line_labels = []
        self.p = None
        super().switch_to_background()
