# NMEA0183 repeater for Raspberry Pi.

So, I was thinking how to connect wind instrument to tiller pilot when
I remembered that I had raspberry pi 3 in drawer. I ordered several RS422
to USB converters from Ebay and wrote python program for moving the bytes
around. For those who don't know, RS422 is serial communication standard,
that is used as nmea0183 transportation layer. Just like CanBus is used
as nmea2000 transportation layer.

I have Yacht Devices nmea0183 to nmea2000 gateway dongle for connecting
repeater to nmea2000 network. That may not be the most optimal solution
as Yacht Devices and other providers have conversion donges with nmea2000
and usb connectors, but as I already have this one, then I use it like this.
Feel free to fork and add a support for those dongles.

One word about chinese RS422 to USB converters. The ones I ordered, have
120 ohm resistor over RX pins, but RS422 standard requires at least 4k ohmes.
So, stronger senders, like my gateway dongle, can wiggle the pins just fine,
but weaker ones, like wind instrument, can't when stand-alone display is also
connected. I added 470 ohm resistor in series with incoming cable, that
resolved the issue. Also, I'm pretty sure that "+" and "-" connector markings
in dongles are another way around. Just beware. The dongles are the ones with
transparent blue cover.

UPDATE 21.08.2021: turns out that 120 ohms is perfectly valid value for line
terminating resistor but Nasa Clipper series instruments expect much higher
values. Series resistor helps in some cases but take a look at my 
nmea0183-5V-buffer-board project for a much more reliable solution.

This program works with any linux with udev, raspberry is not requirement.

With 4 connections working, raspberry pi3 shows about 25% cpu utilization.
