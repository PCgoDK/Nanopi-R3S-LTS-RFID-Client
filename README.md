# Nanopi-R3S-LTS-RFID-Client
Fixed UHF RFID reader with dual Ethernet

## Dokumentation
https://docs.m5stack.com/en/unit/Unit_UHF-RFID
- [NanoPi og UHF-RFID: opsætning og indledende test](docs/nanopi-uhf-test.md)
- [Browserapp: installation, drift og kendte begrænsninger](docs/web-app.md)

## Browserapp

Repoet indeholder nu en browserbaseret RFID-klient med UART-backend til NanoPi'en. Se [installations- og driftsvejledningen](docs/web-app.md), før appen startes på netværket.

Installer webappen som en systemd-tjeneste under `/opt` på NanoPi'en med `sudo sh ./scripts/install-nanopi.sh`. Den lytter lokalt på port 8080; se vejledningen for privat fjernadgang via Tailscale Serve.

Den valgfrie [HDMI-kioskopsætning](docs/web-app.md#hdmi-visning-ved-opstart) åbner appen automatisk på en tilsluttet skærm.

## Status og næste skridt

**Datasniffer er under udvikling.** Navngivne udtræksregler anvendes automatisk på modtagne printdata og kan også afprøves manuelt. Printeradressen kan indstilles, og printjob og RFID-observationer gemmes lokalt i ét døgn; EPC-koblinger foretages manuelt, og loggen kan eksporteres som CSV. RFID-læsning starter ved printdata, genstartes periodisk og kan overse tags; se begrænsningerne i driftsvejledningen. Brugeren har tidligere bekræftet fysisk udskrift og automatisk udtræk; de nye funktioner skal endnu afprøves på udstyret. LLRP-server og videresendelse er endnu ikke implementeret.

Forberedende kode findes i `app/sniffer.py` (udtræksregler), `app/llrp.py` (opbygning af LLRP-tagrapport) og `app/zebra.py` (REST-afsender). LLRP og REST er ikke koblet til fanen eller testet end-to-end mod Zebra-serveren.

Planen for næste udviklingstrin:

1. Fortsæt afprøvning af printeren. Den midlertidige Ethernet-bridge er testet på WAN og LAN: testetiket, webadgang via printerens port 8080, stopknappen, automatisk tilbagerulning og DHCP efter genstart er verificeret. Printerens egen port 8080 kan ikke nås fra hostsiden, mens bridgen er aktiv.
2. Udvid afprøvningen af den passive TCP-opsamling. Den bruger en fast pause på 2 sekunder; flere print uden tilstrækkelig pause kan ikke adskilles sikkert. Udvid senere jobafgrænsningen og afklar usikre job manuelt.
3. Afprøv logning, løbende RFID-læsning, manuel EPC-kobling, `NoTag` og CSV med det fysiske udstyr. Hurtige etiketter og flere etiketter pr. job kan ikke kobles sikkert på baggrund af tid alene.
4. Implementér en LLRP-kompatibel FX7500-emulering på NanoPi'en, og test opdagelse og rapporter mod Zebra RFID Server. Udtræk ønskes også sendt via serverens eksisterende REST-API; der må **ikke** ændres i Zebra-serveren. Serverens begrænsninger, risikoen for dobbelte registreringer og REST-certifikatkrav er beskrevet i [driftsvejledningen](docs/web-app.md).

Bridge-controllerens DHCP, TCP-videresendelse, port 8080 og gendannelse efter procesnedbrud er testet med virtuelle porte. Et separat forsøg gendannede en isoleret NetworkManager-DHCP-profil efter et nedbrud. På de fysiske porte er adgang til printerens port 9100, webadgang via printeradressen, automatisk og manuel tilbagerulning samt DHCP efter genstart verificeret. Brugeren bekræftede, at en Windows-testetiket blev udskrevet, og at printdata blev vist og kunne afprøves med en regel. DHCP-adresseskift og produktionstrafik er ikke testet; TCP-genopbygning ved omordnet trafik og retransmissioner er kun dækket af automatiske tests. Kontrollér læserens region og lokale radioregler før automatisk RFID-læsning; tidligere stod læseren på US-region.

### Aktuel testinstallation

- NanoPi'en har senest været tilgængelig på `192.168.1.132`; DHCP-adressen kan ændres. Webtjenesten er installeret under `/opt/nanopi-rfid-client` og kører som `nanopi-rfid-client.service`.
- På den aktuelle testinstallation er app-token slået fra efter brugerens ønske. Alle med netværksadgang kan styre appen; ukrypteret HTTP må kun bruges på et betroet netværk og aldrig eksponeres på internettet.
- Printerens planlagte adresse er `192.168.1.150:9100`; Zebra-serverens eksempeladresse er `https://192.168.1.151` og skal kunne ændres. Disse adresser er ikke bekræftet som faste.
- Udtræksreglerne kræver den opdaterede backend og installationens skrivbare tilstandsmappe; se [driftsvejledningen](docs/web-app.md). Ingen adgangskoder eller tokens skal gemmes i repoet.

Kør de lokale tests fra repoets rod med `python -m pytest -q`.
