# Browserapp til NanoPi

Browserappen serveres af NanoPi'en og bruger dens UART-forbindelse til M5Stack Unit UHF-RFID. En browser på en anden enhed kan derfor styre læseren uden direkte adgang til browserens egne serielle porte.

## Installation

På NanoPi'en skal brugeren, der kører appen, have adgang til serielporten. På Ubuntu kan det normalt gøres med `dialout`-gruppen:

```sh
sudo usermod -aG dialout "$USER"
```

Log ind igen, så den nye gruppetilhørsforhold træder i kraft. Installer derefter appen fra repoets rod:

```sh
python3 -m venv .venv
. .venv/bin/activate
python -m pip install -r requirements.txt
```

Start lokalt på NanoPi'en:

```sh
python -m app
```

Standardadressen binder kun til `127.0.0.1`. For adgang fra andre enheder på LAN'et skal der sættes et stærkt, privat token og en netværksadresse:

```sh
export RFID_HOST=0.0.0.0
export RFID_PORT=8000
export RFID_APP_TOKEN="$(python -c 'import secrets; print(secrets.token_urlsafe(32))')"
python -m app
```

Åbn derefter `http://<NanoPi-IP>:8000` i en browser på et betroet LAN. Tokenet indtastes i browseren og opbevares kun i den aktuelle browsersession. Del det ikke, og eksponér ikke port 8000 direkte mod internettet. Appen afviser opstart på en ikke-loopback-adresse, hvis `RFID_APP_TOKEN` mangler.

En sikrere mulighed er at lade appen blive på standardadressen `127.0.0.1` og oprette en SSH-tunnel fra browsermaskinen:

```sh
ssh -L 8000:127.0.0.1:8000 <bruger>@<NanoPi-IP>
```

Åbn derefter `http://127.0.0.1:8000` lokalt. Ved direkte LAN-adgang bruges HTTP, så brug kun et betroet netværk eller sæt en TLS-terminerende proxy foran appen.

I fanen **Forbindelse & EPC** vælges den serielle enhed og 115200 baud. Brug om muligt den stabile `/dev/serial/by-id/...`-sti, der er fundet på NanoPi'en; den konkrete sti afhænger af USB-adapteren.

## Funktioner

- De tre faner bruger et kompakt desktoplignende layout inspireret af referencebillederne. På små skærme placeres grupperne under hinanden.
- Seriel forbindelse, hardware- og firmwareversionsforespørgsler, rå UART-rammer og RX/TX-log. Versionerne hentes ved forbindelse eller når en allerede forbundet app åbnes; de kan også opdateres manuelt, når inventering er stoppet.
- Enkelt- og gentagen inventering, EPC-liste, kanal-/regionsindstillinger, effekt og tidsbegrænset CW-test.
- Query- og Select-parametre, læsning og skrivning af taghukommelse samt Lock/Kill- og specialkommandoer.
- Modemparameterforespørgsler og kommandoer til kanal-RSSI og blokeringsscan.

Graferne er tomme, indtil der er modtaget målinger. De viser det kanalinterval og de fortegnede dBm-værdier, som læseren returnerer; de seneste resultater bevares ved faneskift og ændring af vinduets størrelse. Et ugyldigt svar erstatter ikke de tidligere målinger.

Regionerne følger [M5Stacks regionsdefinition](https://github.com/m5stack/M5Unit-RFID/blob/main/src/uhf/uhf.hpp). Japan vises som utilgængelig, da den tilgængelige protokol ikke dokumenterer en regionskode for Japan. Ved regionsvalg beregnes frekvenserne ud fra protokollens kanalformler og de almindelige frekvensbånd; bortset fra China2's 20 kanaler er antallet ikke bekræftet i protokollen. Et valg ændrer ikke læseren, før den tilhørende Sæt-knap bruges. Før kanalskift kontrollerer appen læserens aktuelle region. Frekvensvalg er ikke en bekræftelse på firmwareunderstøttelse eller på, at radioindstillingerne overholder lokale regler.

Skrivning, Lock, Kill, NXP-konfiguration og frekvensændringer kan ændre eller permanent påvirke tags eller læserens indstillinger. Brug et testtag, kontrollér valgt EPC, adgangskoder og parametre, og bekræft handlingerne i appen. Kill og visse låsehandlinger kan ikke fortrydes. CW-bærebølgen slukkes automatisk efter to sekunder; brug kun RF-test i overensstemmelse med lokale regler.

## Kendte begrænsninger

- UI'et følger de tre referencefaner, men enkelte funktioner fra desktopprogrammet er ikke medtaget, hvis kommandoen ikke kunne bekræftes i den tilgængelige protokolreference. **Sæt profil** og **Gem konfiguration** er derfor endnu ikke implementeret. Antennegain og coupler-værdier er kun UI-felter; grafen viser rå måleværdier.
- Antenneskift er ikke medtaget: den dokumenterede M5Stack-enhed har en fast, indbygget antenne.
- RSSI-/blokeringsscanning, taghukommelses-svar og specialkommandoer skal verificeres mod den konkrete læserfirmware på NanoPi'en. Rå svar vises i loggen; nogle specialiserede svar er endnu ikke fortolket til felter.
- Appen er endnu ikke klar til sikker skrivning eller permanent ændring af tags: Select og den efterfølgende tagkommando udføres i separate anmodninger og kan derfor påvirkes af andre samtidige klienter. Brug kun forbindelse og inventering under den første hardwaretest.
- Regionskode og RF-kanalmapping kan variere mellem firmwareversioner. Hent og kontrollér den aktuelle regionskode mod den korrekte hardwaremanual og lokale radioregler, før en regions- eller kanalfunktion ændres. Appen vælger ikke automatisk en region.
- Den 23. november 2026 blev appen startet på NanoPi'en og tilgået via SSH-tunnel. Forbindelse ved 115200 baud gav hardwareversionen `M100 26dBm V1.0`, og én inventering viste to tags. Øvrige funktioner er ikke verificeret på hardware.

Kør de lokale tests fra repoets rod:

```sh
python -m pytest -q
```

## Protokolgrundlag

- [M5Stack Unit UHF-RFID](https://docs.m5stack.com/en/unit/Unit_UHF-RFID)
- [M5Stack UART-kommandodefinitioner](https://github.com/m5stack/M5Unit-UHF-RFID/blob/master/src/CMD.h)
- [M5Stack UHF-RFID-protokol](https://m5stack-doc.oss-cn-shenzhen.aliyuncs.com/842/Unit-RFID-UHF-Protocol-EN.pdf)
