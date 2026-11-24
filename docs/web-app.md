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

Åbn derefter `http://127.0.0.1:8080`. Hvis du ændrer appen til at lytte på en anden adresse end loopback, kræver den som standard et `RFID_APP_TOKEN`; brug kun den mulighed bag en korrekt konfigureret TLS-proxy, og eksponér aldrig ukrypteret HTTP direkte mod internettet.

#### Adgang uden app-token

Adgangskontrollen kan udtrykkeligt slås fra ved at tilføje `RFID_ALLOW_UNAUTHENTICATED=1` til `/etc/nanopi-rfid-client/nanopi-rfid-client.env` og genstarte tjenesten. **Alle, der kan nå appens port, kan derefter bruge alle API'er og styre læseren, også skrive- og Lock/Kill-kommandoer.** Brug kun dette på et betroet, afgrænset netværk, og eksponér aldrig porten mod internettet. Siden viser **Ingen adgangskontrol**, når appens tokenkontrol er slået fra.

Den eksisterende `RFID_APP_TOKEN`-værdi kan bevares i miljøfilen. For at slå adgangskontrollen til igen fjernes linjen `RFID_ALLOW_UNAUTHENTICATED=1`, hvorefter tjenesten genstartes; et token er påkrævet ved binding uden for loopback.

Når et app-token indtastes, gemmes det i browserens lokale lager og bruges automatisk ved senere besøg på samme adresse. Enhver med adgang til browserprofilen kan læse tokenet; brug ikke denne funktion på en delt enhed. Knappen **Glem token** fjerner det fra den aktuelle browserprofil. Fjernelse tilbagekalder ikke tokenet på serveren.

Nyttige servicekommandoer:

```sh
sudo systemctl status nanopi-rfid-client.service
sudo journalctl -u nanopi-rfid-client.service -f
sudo systemctl restart nanopi-rfid-client.service
```

For at opdatere en installation: hent den ønskede version af repoet på NanoPi'en, og kør `sudo sh ./scripts/install-nanopi.sh` fra repoets rod igen. Scriptet bevarer den eksisterende miljøfil og genstarter tjenesten med den opdaterede kode.

### HDMI-visning ved opstart

En valgfri kioskopsætning starter en separat browserbruger automatisk på HDMI-skærmen. Den kræver en eksisterende installation af RFID-tjenesten, en skærm samt tastatur og mus. Installationen bruger LightDM, Openbox og Epiphany og ændrer den lokale grafiske loginopsætning; brug den ikke på en maskine, hvor en eksisterende grafisk session skal bevares.

Installer fra et Linux-checkout på NanoPi'en, med adressen som RFID-tjenesten faktisk lytter på:

```sh
sudo sh ./scripts/install-kiosk.sh http://192.168.1.132:8080/
sudo systemctl start lightdm.service
```

Brug `http://127.0.0.1:8080/`, hvis tjenesten kun lytter på localhost. Adressen i `/etc/nanopi-rfid-client/kiosk-url` indeholder ikke tokenet og skal ændres, hvis tjenestens lytteadresse ændres. Browseren venter på, at appen svarer. Tjenesten er fortsat afhængig af sin eksisterende netværkskonfiguration; en fast indstillet adresse kan ikke bruges, hvis NanoPi'ens IP ændres.

Kiosken bruger en særskilt app-profil for at undgå at gendanne tidligere browservinduer. Hvis tokenkontrol er aktiveret, indtastes app-tokenet første gang, også ved skift fra den tidligere kioskprofil. Appen gemmer det i den lokale browserprofil, så det også bruges efter genstart; det er ikke nødvendigt at gemme det i browserens separate adgangskodelager. Enhver med adgang til skærmen kan betjene appen, og enhver med adgang til browserprofilen kan læse tokenet. HTTP er ukrypteret, så brug kun en LAN-adresse på et betroet netværk.

På den aktuelle NanoPi med 1 GB RAM bruges softwaregrafik og en hukommelsesgrænse på 350 MB for browseren. `zram-tools` er installeret separat med `sudo apt install zram-tools` og aktiverer 256 MB komprimeret swap ved opstart. Det reducerer risikoen for hukommelsesmangel, men garanterer ikke stabil drift ved tung belastning; kontrollér `free -h` og `swapon --show`. Hvis browseren stopper, genstartes den ikke automatisk i den aktuelle session; genstart skærmsessionen efter fejlsøgning:

```sh
sudo systemctl restart lightdm.service
```

For midlertidigt at stoppe skærmvisningen uden at stoppe RFID-tjenesten bruges `sudo systemctl stop lightdm.service`. Hvis den ikke skal starte ved næste boot, kan den stoppes og blokeres med `sudo systemctl mask --now lightdm.service`; fjern blokeringen med `sudo systemctl unmask lightdm.service`. Diagnose findes med `journalctl -u lightdm.service` og i `/home/rfid-display/.xsession-errors`.

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
- Øverst vises den aktuelle dynamiske IPv4-adresse på WAN-porten `eth0`, normalt tildelt via DHCP; visningen opdateres hvert 10. sekund efter eventuelt login. Mangler en dynamisk adresse, vises dette i stedet for en gættet adresse. Opslaget kræver Linux `ip`-værktøjet. Bridge er endnu ikke implementeret, og visning af en LAN-enheds adresse afklares senere.
- Enkelt- og gentagen inventering, EPC-liste, kanal-/regionsindstillinger, effekt og tidsbegrænset CW-test.
- Query- og Select-parametre, læsning og skrivning af taghukommelse samt Lock/Kill- og specialkommandoer.
- Modemparameterforespørgsler og kommandoer til kanal-RSSI og blokeringsscan.

Graferne er tomme, indtil der er modtaget målinger. De viser det kanalinterval og de fortegnede dBm-værdier, som læseren returnerer; de seneste resultater bevares ved faneskift og ændring af vinduets størrelse. Et ugyldigt svar erstatter ikke de tidligere målinger.

Ved enkeltlæsning vises en fejl, hvis intet tag svarer, CRC-kontrollen fejler, eller læseren ikke giver et gyldigt svar. Tidligere aflæste tags bliver i EPC-tabellen, indtil den ryddes.

Ved læsning af taghukommelse vises data separat fra skrivefeltet. Svarets EPC og datalængde kontrolleres mod den valgte læsning; læserfejl og ugyldige svar vises som fejl i stedet for som en vellykket læsning. Resultatet ryddes ved valg af et andet tag, og valget ryddes, hvis tagget fjernes fra tabellen eller forbindelsen afbrydes.

Select-tilstanden kan sættes til alle tre tilstande i M100-manualen, og **Hent Select** udfylder parametrene. Lock tilbyder også permanent åben; både den og permanent låsning kan være uigenkaldelige. QT Read/Write og flygtig/ikke-flygtig lagring følger manualens værdier. NXP ChangeConfig bruger en bitmaske, hvor `1` inverterer den tilsvarende bit og `0000` læser uden at ændre den.

NXP-kommandoerne kræver kompatible G2X-tags og QT kræver et understøttet Monza 4 QT-tag. BlockPermalock-rammen følger manualens eksempel med User-bank `03`; manualens tekst og eksempel er indbyrdes modstridende. Manualens EAS-eksempel er også modstridende. Disse specialkommandoer og Lock/Kill er ikke hardwareverificeret; kontrol af svaret dokumenterer ikke, at en anden firmware bruger de samme kommandoer.

Fanen **Datasniffer** kan gemme op til 100 navngivne udtræksregler og afprøve dem på manuelt indtastet tekst. Vælg en regel i listen for at redigere eller fjerne den, eller vælg **Ny regel**. Reglerne deles mellem appens brugere og bevares efter genstart. Samtidige ændringer af samme regel afvises med en fejl; opdatér listen og vælg reglen igen. Udtrækket søger efter første forekomst af en tekst og bruger enten et angivet antal Unicode-tegn eller teksten frem til første afslutningstekst, som ikke medtages. Manglende, ufuldstændige eller ugyldige UTF-8-udtræk markeres; den manuelle tekstprøve kan ikke indtaste binære data.

Ved systemd-installation gemmes reglerne i `/var/lib/nanopi-rfid-client/sniffer/sniffer-rules.sqlite3`. Installationsscriptet opretter en skrivbar mappe til tjenestebrugeren. Ved manuel kørsel bruges som standard `~/.local/state/nanopi-rfid-client`; `RFID_STATE_DIR` kan sættes til en anden skrivbar mappe. Tag sikkerhedskopi af databasen, når tjenesten er stoppet. Testtekst sendes til appen, men gemmes ikke af prøvefunktionen.

Bridge-controlleren kan installeres separat med `sudo sh ./scripts/install-bridge.sh`, efter at den opdaterede webapp og pakkerne `busybox-static` og `nftables` er installeret. Den kræver systemd, NetworkManager og systemd-resolved. Webappen skal lytte på `0.0.0.0:8080`; dette gør den tilgængelig på alle IPv4-grænseflader. Brug adgangskontrol og TLS, eller vælg kun kodefri HTTP på et betroet netværk; eksponér aldrig appen på internettet. Installationen starter kun controlleren, ikke bridgen.

Indstil printerens IPv4-adresse og TCP-port i feltet **Printer TCP/IP**, og gem, mens bridgen er stoppet. Standard er `192.168.1.150:9100`; port 8080 er reserveret til bridge-bekræftelsen. Indstillingen bevares efter genstart. Kontrollér den faktiske printeradresse før start.

**Start bridge** frigiver WAN og LAN fra NetworkManager uden at ændre deres profiler, opretter en midlertidig bridge og henter DHCP på den. Åbn den valgte printeradresse på port 8080 via linket i fanen fra hostsiden inden to minutter for at bekræfte adgangen; ellers stopper en uafhængig timer bridgen og forsøger at gendanne de oprindelige netværksprofiler. Via samme adresse kan bridgen stoppes. Printerens egen port 8080 kan ikke nås fra hostsiden, mens omdirigeringen er aktiv. Et nedbrud i appen, controlleren eller NetworkManager stopper også bridgesessionen. Bridge-sessionen er ikke aktiveret ved boot; efter genstart bruges WAN-profilens DHCP. DHCP-adressen kan ændres. Kontrollér tjenesteloggen, hvis gendannelsen fejler, og hav lokal skærm og tastatur klar ved fysisk afprøvning.

Under en aktiv bridge starter en separat, uprivilegeret tjeneste med `CAP_NET_RAW` og opsamler passivt trafik fra WAN til den valgte printeradresse og TCP-port. De gemte regler anvendes automatisk på hvert opsamlet job, også når ingen browser er åben. Tjenesten læser reglerne på tidspunktet for behandlingen; senere regelændringer anvendes ikke på tidligere job. Testfeltet og udtrækket viser kun det seneste job og erstatter det eksisterende indhold; en manuel prøve er stadig mulig. En pause på 2 sekunder eller en TCP-afslutning bruges til at afgrænse data; flere hurtige print kan samles, og en lang pause kan dele et job. Brugeren har bekræftet fysisk udskrift, visning af printdata og automatisk udtræk med standardadressen.

Opsamlingen forsøger at ordne TCP-segmenter og fjerne gentagelser. Manglende segmenter, konflikter, observeret pakketab, IP-fragmentering og over 100.000 bytes markeres som potentielt ufuldstændige; automatiske udtræk fra sådanne data markeres usikre og viser ikke værdier. Der kan også være tab, som ikke kan opdages; brug ikke visningen som garanti for fuldstændige printdata. Binære eller ugyldige UTF-8-data erstattes i visningen og kan ikke afprøves præcist manuelt; det automatiske udtræk anvender de oprindelige bytes. Den midlertidige behandlingskø er begrænset til 16 job; fejl og køoverløb vises i fanen og logges. Tjenestefejl ændrer ikke den transparente videresendelse.

Job og RFID-observationer gemmes i `/var/lib/nanopi-rfid-client/sniffer/jobs.sqlite3` og slettes automatisk, når de er over 24 timer gamle. Oprydning foretages, mens appen kører, og genoptages efter en genstart. Rå printdata gemmes ikke i jobloggen. Data i de midlertidige køer kan gå tabt ved strømsvigt, nedbrud, køoverløb eller lagringsfejl; kontroller advarsler i fanen og tjenesteloggen. Jobloggen viser de seneste 100 job og 200 RFID-observationer; CSV-eksporten omfatter alle gemte job og indeholder én række pr. manuelt tilknyttet EPC, eller én tom EPC-række for et uafklaret job. Importér EPC-kolonnen som tekst i regneark.

Når bridgen er aktiv og læseren er forbundet, starter appen inventering ved de første registrerede data i et printjob og gemmer observerede EPC'er med tidspunkter. Læsningen fortsætter efter jobpausen, indtil næste job starter eller bridgen stoppes; ved et nyt job stoppes den forrige inventering først. Appen kontrollerer opsamlingen cirka hvert 0,1 sekund, og læseren skal derefter starte, så et hurtigt tag kan passere, før læsningen er i gang. Protokollen begrænser en inventering til 65.535 runder og beskriver ikke et sikkert slutsignal. Appen stopper og genstarter derfor også sin inventering cirka hvert 30. sekund; der er en pause, hvor tags kan overses. Tælleren kan desuden udløbe inden genstarten, og et modtaget svar garanterer ikke, at læsningen fortsat er aktiv. Intervallet og læserens adfærd skal afprøves fysisk; der er ingen garanti for en læsning pr. etiket. Hvis manuel inventering allerede kører, overtager appen den ikke. Mens Datasnifferens inventering kører, kan almindelige læserkommandoer ikke anvendes; stop bridgen først. Når bridgen stopper, stopper appen kun sin egen inventering. Hvis læseren er afbrudt, bruges manuelt eller giver fejl, vises dette i fanen, og manglende læsninger er ikke bevis for fravær af tags.

Der foretages ingen automatisk sammenkobling: hurtige etiketter, flere etiketter pr. job og ens EPC'er kan ikke kobles sikkert med TCP-tidspunkter alene. Vælg et job og en observeret EPC, og bekræft koblingen manuelt; flere observationer kan knyttes til samme job. **NoTag** er udelukkende en manuel markering og kan fortrydes. Gentagne læsninger af samme EPC med højst 0,5 sekund imellem samles i én observation, så identiske etiketter kan ikke altid adskilles. Logning og løbende RFID-læsning kræver fysisk afprøvning.

Isolerede forsøg med virtuelle porte har verificeret controllerens DHCP, standardrute, TCP-videresendelse, port 8080 og gendannelse efter tvangsafslutning. DNS-kald og NetworkManager-gendannelse blev simuleret i controllertesten; et separat forsøg gendannede en rigtig, men isoleret, NetworkManager-DHCP-profil efter nedbrud. Testene brugte HTTP på printerportene og omfattede ikke en fysisk printer, DHCP-adresseskift eller de faktiske netværksprofiler.

På den aktuelle NanoPi er de fysiske porte efterfølgende testet: port 9100 og webappen via printeradressen var tilgængelige, og både timer og stopknap gendannede WAN-profilerne. En genstart med aktiv bridge gav efterfølgende bridge fra og DHCP på WAN. Windows' standardtestside blev afvist med kode 5; en alternativ Windows-testetiket blev sendt, og brugeren bekræftede, at den blev udskrevet.

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

Åbn desuden [test_memory_ui.html](../tests/test_memory_ui.html) i en browser for at teste Læs & Skriv-fanens protokolrammer uden en læser. Siden skal vise `PASS`.

## Protokolgrundlag

- [M5Stack Unit UHF-RFID](https://docs.m5stack.com/en/unit/Unit_UHF-RFID)
- [M5Stack UART-kommandodefinitioner](https://github.com/m5stack/M5Unit-UHF-RFID/blob/master/src/CMD.h)
- [M5Stack UHF-RFID-protokol](https://m5stack-doc.oss-cn-shenzhen.aliyuncs.com/842/Unit-RFID-UHF-Protocol-EN.pdf)
