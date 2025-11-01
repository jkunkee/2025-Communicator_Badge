"""Template app for badge applications. Copy this file and update to implement your own app."""

import uasyncio as aio  # type: ignore

from apps.base_app import BaseApp
from apps import scd30
from net.net import register_receiver, send, BROADCAST_ADDRESS
from net.protocols import Protocol, NetworkFrame
from ui.page import Page
import ui.styles as styles
import lvgl

"""
All protocols must be defined in their apps with unique ports. Ports must fit in uint8.
Try to pick a protocol ID that isn't in use yet; good luck.
Structdef is the struct library format string. This is a subset of cpython struct.
https://docs.micropython.org/en/latest/library/struct.html
"""
ATMOS_PROTOCOL = Protocol(port=25, name="AtmosphereData", structdef="!Bfff")


class App(BaseApp):
    """Define a new app to run on the badge."""

    def __init__(self, name: str, badge):
        """ Define any attributes of the class in here, after super().__init__() is called.
            self.badge will be available in the rest of the class methods for accessing the badge hardware.
            If you don't have anything else to add, you can delete this method.
        """
        super().__init__(name, badge)
        # You can also set the sleep time when running in the foreground or background. Uncomment and update.
        # Remember to make background sleep longer so this app doesn't interrupt other processing.
        self.foreground_sleep_ms = 10
        self.background_sleep_ms = 2000
        if 0x61 in self.badge.sao_i2c.scan():
            self.scd30 = scd30.SCD30(self.badge.sao_i2c, 0x61, 5000)
            self.scd30.set_measurement_interval(2)
            #self.scd30.start_continous_measurement(950)
        else:
            self.scd30 = None
        self.measurement = None
        self.screen_has_latest_data = True

    def start(self):
        """ Register the app with the system.
            This is where to register any functions to be called when a message of that protocol is received.
            The app will start running in the background.
            If you don't have anything else to add, you can delete this method.
        """
        super().start()
        register_receiver(ATMOS_PROTOCOL, self.receive_message)

    def receive_message(self, message: NetworkFrame):
        """Handle incoming messages."""
        print(message)

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
                                                    int(0), # version
                                                    float(self.measurement[0]), # ppm CO2
                                                    float(self.measurement[1]), # deg C
                                                    float(self.measurement[2]), # percent relative humidity
                                                ))
                self.badge.lora.send(tx_msg)
        except:
            print("scd30 read failure")

    def run_foreground(self):
        """ Run one pass of the app's behavior when it is in the foreground (has keyboard input and control of the screen).
            You do not need to loop here, and the app will sleep for at least self.foreground_sleep_ms milliseconds between calls.
            Don't block in this function, for it will block reading the radio and keyboard.
            If the app only runs in the background, you can delete this method.
        """
        self.poll_data()
        if self.scd30:
            if not self.screen_has_latest_data:
                self.screen_has_latest_data = True
                self.p.infobar_left.set_text(str(self.measurement))
        else:
            self.p.infobar_left.set_text("Device not present")

        if self.badge.keyboard.f1():
            print("Hello ")
        if self.badge.keyboard.f2():
            print("World.  ")
        if self.badge.keyboard.f3():
            print("READ MORE ")
        if self.badge.keyboard.f4():
            print("HACKADAY!")
        ## Co-op multitasking: all you have to do is get out
        if self.badge.keyboard.f5():
            self.badge.display.clear()
            self.switch_to_background()

    def run_background(self):
        """ App behavior when running in the background.
            You do not need to loop here, and the app will sleep for at least self.background_sleep_ms milliseconds between calls.
            Don't block in this function, for it will block reading the radio and keyboard.
            If the app only does things when running in the foreground, you can delete this method.
        """
        super().run_background()
        self.poll_data()

    def switch_to_foreground(self):
        """ Set the app as the active foreground app.
            This will be called by the Menu when the app is selected.
            Any one-time logic to run when the app comes to the foreground (such as setting up the screen) should go here.
            If you don't have special transition logic, you can delete this method.
        """
        super().switch_to_foreground()
        self.p = Page()
        ## Note this order is important: it renders top to bottom that the "content" section expands to fill empty space
        ## If you want to go fully clean-slate, you can draw straight onto the p.scr object, which should fit the full screen.
        self.p.create_infobar(["My First App", "Prints to Serial Console"])
        self.p.create_content()
        self.p.create_menubar(["Hello", "World", "Read more", "Hackaday", "Done"])
        self.p.replace_screen()
        self.screen_has_latest_data = False


    def switch_to_background(self):
        """ Set the app as a background app.
            This will be called when the app is first started in the background and when it stops being in the foreground.
            If you don't have special transition logic, you can delete this method.
        """
        self.p = None
        super().switch_to_background()


