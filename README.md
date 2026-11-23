# Nanopi-R3S-LTS-RFID-Client
Fixed UHF RFID reader with dual Ethernet

## Dokumentation
https://docs.m5stack.com/en/unit/Unit_UHF-RFID
- [NanoPi og UHF-RFID: opsætning og indledende test](docs/nanopi-uhf-test.md)
- [Browserapp: installation, drift og kendte begrænsninger](docs/web-app.md)

## Browserapp

Repoet indeholder nu en browserbaseret RFID-klient med UART-backend til NanoPi'en. Se [installations- og driftsvejledningen](docs/web-app.md), før appen startes på netværket.

Installer webappen som en systemd-tjeneste under `/opt` på NanoPi'en med `sudo sh ./scripts/install-nanopi.sh`. Den lytter lokalt på port 8080; se vejledningen for privat fjernadgang via Tailscale Serve.
