import asyncio

KEYS = ('UP', 'DOWN', 'OK', 'BACK', 'AUX')


class Buttons:
    def __init__(self, config, publish):
        self.config, self.publish = config, publish
        self.devices = []
        self.factory = None

    async def start(self):
        if not self.config.get('enabled'):
            return
        pins = self.config.get('pins', {})
        if set(pins) != set(KEYS) or any(type(p) is not int or not 0 <= p <= 27 for p in pins.values()):
            raise ValueError('Configure exactly five BCM GPIO pins: UP, DOWN, OK, BACK, AUX')
        if len(set(pins.values())) != 5 or set(pins.values()) & set(self.config.get('reserved_pins', [])):
            raise ValueError('Button GPIO pins overlap each other or reserved screen pins')
        from gpiozero import Button
        from gpiozero.pins.lgpio import LGPIOFactory
        chip = self.config.get('chip')
        self.factory = LGPIOFactory() if chip is None else LGPIOFactory(chip=chip)
        loop = asyncio.get_running_loop()
        def callback(key, down):
            def send():
                asyncio.create_task(self.publish('pi', 'button', {'key': key, 'down': down}, persist=False))
            loop.call_soon_threadsafe(send)
        for key, pin in pins.items():
            button = Button(pin, pull_up=True, bounce_time=.05, pin_factory=self.factory)
            button.when_pressed = lambda k=key: callback(k, True)
            button.when_released = lambda k=key: callback(k, False)
            self.devices.append(button)

    def close(self):
        for device in self.devices:
            device.close()
        if self.factory:
            self.factory.close()