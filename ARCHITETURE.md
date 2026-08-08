# Project architethre:

 - src/insertplay-cli: C# .net8 solution that implements insertplay CLI, following Clean Architecture (dependency rule points inward: Domain <- Application <- Infrastructure/Presentation)
   - InsertPlay.Cli.csproj: presentation/composition root (Program.cs, Commands/, CommandRouter) - the published executable
   - InsertPlay.Cli.Domain: entities and pure business rules (AppConfig, SteamShortcutEntry, AppIdCalculator, GameIdCalculator), zero dependencies
   - InsertPlay.Cli.Application: use cases (one per CLI command) and the abstractions (interfaces) Infrastructure implements
   - InsertPlay.Cli.Infrastructure: concrete implementations (Steam VDF/registry, JSON config, process execution)
   - InsertPlay.Cli.Tests: xUnit unit tests (fakes for Application's abstractions, plus round-trip tests for the VDF binary format and the runtime .env file format)
 - src/insertplay-service: scripts sh that implements insertplay service (insertplay-service.sh and insertplay-start-bootstrap.sh)
 - src/insertplay-sdcard: scripts sh used on sd cards (autorun.sh, insertplay-start.sh, install-shortcut.sh)
 - src/publish.sh: script used to publish insertplay to 'publish' folder
 - src/build-sd-card.sh: script used to build and copy insertplay to sd-card

# Application architeture:

Application output expected architeture:

 - insertplay: main folder
 - insertplay/Insertplay.Cli: cli executable
 - insertplay/insertplay-service.sh: service script
 - insertplay/install.sh: install script
 - insertplay/uninstall.sh: uninstall script
 - assets/start-sd-capsule.png: insertplay steam shortcut artwork
 - assets/start-sd-horizontal.png: insertplay steam shortcut artwork
 - assets/start-sd-icon.png: insertplay steam shortcut artwork
 - assets/start-sd-logo.png: insertplay steam shortcut artwork
 