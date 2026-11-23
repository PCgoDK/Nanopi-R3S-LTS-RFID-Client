# Browserapp til NanoPi

Browserappen serveres af NanoPi'en og bruger dens UART-forbindelse til M5Stack Unit UHF-RFID. En browser på en anden enhed kan derfor styre læseren uden direkte adgang til browserens egne serielle porte.

## Installation

### Installer som systemtjeneste under `/opt`

På Ubuntu/Debian installerer dette script appen i `/opt/nanopi-rfid-client`, opretter et Python-virtualmiljø, og installerer samt aktiverer `nanopi-rfid-client.service`. Tjenesten kører som den dedikerede systembruger `rfid-client`, som tilføjes gruppen `dialout` for adgang til USB-UART-læseren.

```sh
sudo apt update
sudo apt install -y git python3 python3-venv
git clone https://github.com/PCgoDK/Nanopi-R3S-LTS-RFID-Client.git
cd Nanopi-R3S-LTS-RFID-Client
sudo sh ./scripts/install-nanopi.sh
```

Installeringsscriptet skal køres som root på en systemd-baseret Ubuntu/Debian-installation. Det ændrer ikke Ethernet- eller firewallindstillinger. Standardkonfigurationen i `/etc/nanopi-rfid-client/nanopi-rfid-client.env` binder kun til `127.0.0.1:8080`; porten er derfor ikke direkte tilgængelig fra LAN eller internettet.

```sh
sudo systemctl status nanopi-rfid-client.service
```

### Privat fjernadgang via Tailscale

Tailscale Serve giver HTTPS-adgang fra enheder, der er godkendt på dit private Tailscale-netværk (tailnet), uden at åbne en port i routeren. Det gør ikke appen offentligt tilgængelig på internettet. Installer Tailscale på NanoPi'en og de klientenheder, der skal have adgang, og log dem ind på samme tailnet. Følg [Tailscales Linux-installationsvejledning](https://tailscale.com/kb/1031/install-linux).

```sh
sudo tailscale up
sudo tailscale serve --bg 8080
sudo tailscale serve status
```

Følg eventuelle login- eller HTTPS-samtykketrin, som Tailscale viser. Åbn derefter den `https://…ts.net`-adresse, som `tailscale serve status` viser, på en anden enhed i tailnettet. Tailscale Serve-konfigurationen fortsætter efter genstart. Adgangen følger tailnettets adgangskontrol; del ikke enheden med personer, der ikke skal kunne styre RFID-læseren. Brug **ikke** Tailscale Funnel, hvis adgangen skal forblive privat.

Hvis du tidligere har installeret appen med den gamle port, ret `RFID_PORT=8000` til `RFID_PORT=8080` i `/etc/nanopi-rfid-client/nanopi-rfid-client.env`, og genstart tjenesten. Behold `RFID_HOST=127.0.0.1`, så Tailscale er den eneste fjernadgangsvej.

En midlertidig SSH-tunnel kan stadig bruges til lokal adgang:

```sh
ssh -L 8080:127.0.0.1:8080 <bruger>@<NanoPi-IP>
```

Åbn derefter `http://127.0.0.1:8080`. Hvis du ændrer appen til at lytte på en anden adresse end loopback, kræver den et `RFID_APP_TOKEN`; brug kun den mulighed bag en korrekt konfigureret TLS-proxy, og eksponér aldrig ukrypteret HTTP direkte mod internettet.

Når et app-token indtastes, gemmes det i browserens lokale lager og bruges automatisk ved senere besøg på samme adresse. Enhver med adgang til browserprofilen kan læse tokenet; brug ikke denne funktion på en delt enhed. Knappen **Glem token** fjerner det fra den aktuelle browserprofil. Fjernelse tilbagekalder ikke tokenet på serveren.

Nyttige servicekommandoer:

```sh
sudo systemctl status nanopi-rfid-client.service
sudo journalctl -u nanopi-rfid-client.service -f
sudo systemctl restart nanopi-rfid-client.service
```

For at opdatere en installation: hent den ønskede version af repoet på NanoPi'en, og kør `sudo sh ./scripts/install-nanopi.sh` fra repoets rod igen. Scriptet bevarer den eksisterende miljøfil og genstarter tjenesten med den opdaterede kode.

### Manuel udviklingskørsel

Til lokal udvikling uden systemd-tjeneste kan appen stadig startes direkte:

```sh
python3 -m venv .venv
. .venv/bin/activate
python -m pip install -r requirements.txt
python -m app
```

I fanen **Forbindelse & EPC** vælges den serielle enhed og 115200 baud. Brug om muligt den stabile `/dev/serial/by-id/...`-sti, der er fundet på NanoPi'en; den konkrete sti afhænger af USB-adapteren.

## Funktioner

- De tre faner bruger et kompakt desktoplignende layout inspireret af referencebillederne. På små skærme placeres grupperne under hinanden.
- Seriel forbindelse, hardware- og firmwareversionsforespørgsler, rå UART-rammer og RX/TX-log. Versionerne hentes ved forbindelse eller når en allerede forbundet app åbnes; de kan også opdateres manuelt, når inventering er stoppet.
- Enkelt- og gentagen inventering, EPC-liste, kanal-/regionsindstillinger, effekt og tidsbegrænset CW-test.
- Query- og Select-parametre, læsning og skrivning af taghukommelse samt Lock/Kill- og specialkommandoer.
- Modemparameterforespørgsler og kommandoer til kanal-RSSI og blokeringsscan.

Graferne er tomme, indtil der er modtaget målinger. De viser det kanalinterval og de fortegnede dBm-værdier, som læseren returnerer; de seneste resultater bevares ved faneskift og ændring af vinduets størrelse. Et ugyldigt svar erstatter ikke de tidligere målinger.

Fanen **Datasniffer** er en designvisning med eksempeldata; dens kontroller er deaktiveret og ændrer ikke netværket eller læseren. Regellisten viser én eksempelregel og er beregnet til flere navngivne regler. Planen er, at bridgens Start/Stop-tilstand huskes efter genstart, og at trafik fra hostsiden til printerens port 8080 omdirigeres til webappen; printerens egen tjeneste på den port kan dermed ikke nås fra hostsiden. Den foreløbige udtrækslogik søger efter første forekomst af en tekst og uddrager enten et angivet antal Unicode-tegn eller teksten frem til første afslutningstekst, som ikke medtages. Binære data uden for udtrækket kan forekomme; manglende, ufuldstændige eller ugyldige UTF-8-udtræk markeres som fejl. TCP-opsamling, jobinddeling, RFID-kobling, lagring, CSV, omdirigering og bridgestyring er endnu ikke implementeret.

Den planlagte integration skal lade NanoPi'en optræde som en emuleret FX7500 via LLRP og sende EPC'er til Zebra RFID Server. LLRP-serveren og forbindelsen til læseren er endnu ikke implementeret. Udtræk skal sendes separat via serverens eksisterende `POST /api/v1/tag-reads` over HTTPS og et bearer-token. Et oprettet læser-ID og en gyldig EPC er påkrævet; udtrækkene medsendes som JSON i `raw_payload`. De to afsendelser kan give dobbelte registreringer. Serverens aktuelle API og portal viser ikke udtrækket, og serveren bevarer kun første og seneste post pr. læser/EPC. `NoTag` kan ikke indsendes, så den fulde log skal bevares lokalt. En intern CA skal være betroet af NanoPi'en eller angives til REST-afsenderen; certifikatkontrol slås ikke fra. Automatisk kø, manuel gensendelse og brugerindstillinger er endnu ikke implementeret, og ingen data afsendes fra designvisningen. Ved timeout eller serverfejl kan det være uklart, om en post blev gemt; gentag ikke ukritisk uden at afklare det.

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
