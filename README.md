# Nanopi-R3S-LTS-RFID-Client
Fixed UHF RFID reader with dual Ethernet

## Dokumentation
https://docs.m5stack.com/en/unit/Unit_UHF-RFID
- [NanoPi og UHF-RFID: opsætning og indledende test](docs/nanopi-uhf-test.md)
- [Browserapp: installation, drift og kendte begrænsninger](docs/web-app.md)

## Browserapp

Repoet indeholder nu en browserbaseret RFID-klient med UART-backend til NanoPi'en. Se [installations- og driftsvejledningen](docs/web-app.md), før appen startes på netværket.

Installer webappen som en systemd-tjeneste under `/opt` på NanoPi'en med `sudo sh ./scripts/install-nanopi.sh`. Den lytter lokalt på port 8080; se vejledningen for privat fjernadgang via Tailscale Serve.

## Status og næste skridt

**Datasniffer er endnu kun en designvisning.** Felterne og knapperne på fanen er deaktiverede, og de viste printdata, udtræk og EPC er eksempler. Der er ikke etableret bridge, TCP-opsamling, joblog, CSV-eksport, RFID-kobling eller en LLRP-server.

Forberedende kode findes i `app/sniffer.py` (udtræksregler), `app/llrp.py` (opbygning af LLRP-tagrapport) og `app/zebra.py` (REST-afsender). Delene er dækket af lokale tests, men er ikke koblet til fanen eller testet end-to-end mod Zebra-serveren.

Planen for næste udviklingstrin:

1. Implementér en sikker, transparent Ethernet-bridge mellem WAN (host) og LAN (printer). Start/Stop skal huskes efter genstart. Omdirigér kun printerens port 8080 på hostsiden til webappen; printerens egen port 8080 vil da ikke kunne nås fra hostsiden. Test først i et isoleret netværk og med automatisk tilbagerulning: den aktuelle NanoPi administreres kun via SSH.
2. Opsaml trafik til printerens TCP-port 9100, saml TCP-strømmen korrekt og afgræns job med en justerbar pause. Flere print uden tilstrækkelig pause kan ikke adskilles sikkert og skal markeres til manuel afklaring. Printdata må ikke ændres på vej til printeren.
3. Implementér og gem flere navngivne regler med enten antal tegn eller afslutningstekst. Vis seneste printjob i det scrollbare vindue, og gem udtræk og EPC i en vedvarende log med CSV-eksport. Job skal køes; manglende tag markeres `NoTag`, og usikre koblinger afklares manuelt.
4. Implementér en LLRP-kompatibel FX7500-emulering på NanoPi'en, og test opdagelse og rapporter mod Zebra RFID Server. Udtræk ønskes også sendt via serverens eksisterende REST-API; der må **ikke** ændres i Zebra-serveren. Serverens begrænsninger, risikoen for dobbelte registreringer og REST-certifikatkrav er beskrevet i [driftsvejledningen](docs/web-app.md).

En isoleret virtuel bridge og passiv opsamling af én Ethernet-ramme er testet på NanoPi'en uden at ændre de fysiske porte. Det er ikke en test af TCP-genopbygning eller produktionstrafik. Kontrollér læserens region og lokale radioregler før automatisk RFID-læsning; tidligere stod læseren på US-region.

### Aktuel testinstallation

- NanoPi'en har senest været tilgængelig på `192.168.1.132`; DHCP-adressen kan ændres. Webtjenesten er installeret under `/opt/nanopi-rfid-client` og kører som `nanopi-rfid-client.service`.
- Tjenestens lokale konfiguration er ændret til at lytte på NanoPi'ens LAN-adresse på port 8080 med et app-token. Adgang via HTTP er ukrypteret og må kun bruges på et betroet netværk. Bindingsændringen betyder, at den tidligere Tailscale Serve-opsætning mod localhost ikke virker uden omkonfiguration.
- Printerens planlagte adresse er `192.168.1.150:9100`; Zebra-serverens eksempeladresse er `https://192.168.1.151` og skal kunne ændres. Disse adresser er ikke bekræftet som faste.
- Designfilerne er kopieret til den kørende installation. De nye Python-moduler er endnu ikke installeret eller aktiveret på NanoPi'en. Ingen adgangskoder eller tokens skal gemmes i repoet.

Kør de lokale tests fra repoets rod med `python -m pytest -q`.
