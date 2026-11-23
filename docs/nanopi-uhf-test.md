# NanoPi og UHF-RFID: indledende test

Testet 21. november 2026.

## Udstyr og forbindelse
https://docs.m5stack.com/en/unit/Unit_UHF-RFID
- FriendlyElec NanoPi R3S LTS med Ubuntu 24.04.5 LTS, aarch64 og Linux 6.1.141.
- M5Stack Unit UHF-RFID U107, som ved forespørgsel på hardwareversion svarede `M100 26dBm V1.0`.
- Silicon Labs CP2102 USB-til-UART-adapter, USB-id `10c4:ea60`. Den blev registreret som `/dev/ttyUSB0`.
- Læseren blev forsynet via USB-adapteren og kommunikerede med 115200 baud, 8 databits, ingen paritet og 1 stopbit (8N1).

M5Stack angiver følgende Grove-ledninger set fra læseren: sort = GND, rød = 5 V, gul = UART RX og hvid = UART TX. Forbind læserens RX til adapterens TX og læserens TX til adapterens RX, og kontrollér adapterens signalniveau og strømforsyning før tilslutning.

Ved testen fandtes også en stabil enhedssti:

```text
/dev/serial/by-id/usb-Silicon_Labs_CP2102_USB_to_UART_Bridge_Controller_0001-if00-port0
```

Stien og `/dev/ttyUSB0` kan variere med adapter og system. Læseren svarede via UART; en selvstændig ikke-interaktiv SSH-forbindelse blev afvist af autentificeringen, men testen kunne udføres i en eksisterende SSH-session.

## Region

Læseren rapporterede først region `01` (Kina 900 MHz). Efter godkendelse blev den ændret til `03` (Europa). En efterfølgende forespørgsel returnerede:

```text
BB 01 08 00 01 03 0D 7E
```

Indstillingen kan være vedvarende; kontrollér region og lokale radioregler før brug. Region `03` alene dokumenterer ikke, at alle radioindstillinger overholder de lokale krav.

## Læsetest

En test på 60,0 sekunder sendte gentagne enkeltinventeringskommandoer med cirka 0,3 sekunders interval. Den registrerede:

- 220 svarrammer i alt.
- 129 taglæsninger fordelt på 12 forskellige EPC'er.
- 91 øvrige svar registreret under kode `15`; deres betydning blev ikke afklaret.

Testen viser, at UART-forbindelsen og læsning af tags virkede i den konkrete opstilling. Den er ikke en rækkevidde- eller stabilitetstest over længere tid. Selve testprogrammet blev kørt midlertidigt og er ikke gemt i repositoriet.

M5Stack oplyser, at antennen er fast afstemt til 922 MHz, og at frekvenser længere fra denne kan reducere læseafstanden. Maksimal rækkevidde og faktisk sendeeffekt blev ikke målt.

## Anvendte UART-kommandoer

Hexadecimale bytes, fra M5Stacks kommandoliste:

| Funktion | Kommando |
| --- | --- |
| Hent hardwareversion | `BB 00 03 00 01 00 04 7E` |
| Hent region | `BB 00 08 00 00 08 7E` |
| Sæt region til Europa | `BB 00 07 00 01 03 0B 7E` |
| Enkeltinventering | `BB 00 22 00 00 22 7E` |

## Kilder

- [M5Stack Unit UHF-RFID U107](https://docs.m5stack.com/en/unit/Unit_UHF-RFID)
- [M5Stacks UART-kommandoer](https://github.com/m5stack/M5Unit-UHF-RFID/blob/master/src/CMD.h)
- [M5Stacks læserbibliotek](https://github.com/m5stack/M5Unit-UHF-RFID/blob/master/src/UNIT_UHF_RFID.cpp)
