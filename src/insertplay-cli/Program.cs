using System.Text.Json;
using System.Diagnostics;
using InsertPlay.Cli.Config;
using InsertPlay.Cli.Models;
using InsertPlay.Cli.Steam;

var configService = new ConfigService();
var shortcutService = new ShortcutService();
var shortcutIdentityService = new ShortcutIdentityService();
const string StartSdAppName = "SD CARD";
const string StartSdExePath = "/bin/bash";

if (args.Length == 0)
{
    PrintHelp();
    return 0;
}

return args[0] switch
{
    "setup" => RunSetup(configService),
    "users" => RunUsers(configService),
    "configure" => RunConfigure(args, configService),
    "shortcut" => RunShortcut(args, configService, shortcutService),
    "steam" => RunSteam(args, configService),
    "runtime" => RunRuntime(args, configService, shortcutService, shortcutIdentityService),
    "help" or "--help" or "-h" => PrintAndExit(0),
    _ => PrintAndExit(1)
};

int RunSetup(ConfigService configSvc)
{
    var steamRoot = SteamPaths.FindSteamRoot();
    if (string.IsNullOrWhiteSpace(steamRoot))
    {
        Console.Error.WriteLine("Steam not found.");
        return 2;
    }

    var users = SteamPaths.ListUsers(steamRoot);
    if (users.Count == 0)
    {
        Console.Error.WriteLine("No Steam users found.");
        return 3;
    }

    Console.WriteLine("Steam users found:\n");
    for (var i = 0; i < users.Count; i++)
        Console.WriteLine($"[{i}] {users[i].PersonaName} ({users[i].UserId})");

    Console.Write("\nChoose a user: ");
    var choice = Console.ReadLine();
    if (!int.TryParse(choice, out var index) || index < 0 || index >= users.Count)
    {
        Console.Error.WriteLine("Invalid index.");
        return 4;
    }

    var selected = users[index];
    var existing = configSvc.TryLoad();
    var cfg = existing ?? new AppConfig();

    cfg.SteamUserId = selected.UserId;
    cfg.SteamPath = steamRoot;
    cfg.SchemaVersion = 1;

    configSvc.Save(cfg);
    Console.WriteLine($"Configuration saved to {configSvc.GetConfigPath()}");

    BootstrapStartSdShortcut(cfg, shortcutService, configSvc);
    return 0;
}

void BootstrapStartSdShortcut(AppConfig cfg, ShortcutService service, ConfigService configSvc)
{
    if (!OperatingSystem.IsLinux())
    {
        Console.WriteLine("SD CARD shortcut: skipped (only supported on Linux at this stage).");
        return;
    }

    var scriptPath = ResolveOutputFilePath("insertplay-start-bootstrap.sh");

    if (scriptPath is null)
    {
        Console.Error.WriteLine("SD CARD shortcut: local script not found in the execution directory.");
        return;
    }

    var iconPath = ResolveOutputFilePath(Path.Combine("assets", "start-sd-icon.png")) ?? string.Empty;

    var appId = AppIdCalculator.Calculate(StartSdExePath, StartSdAppName);

    var ok = service.AddOrUpdate(
        cfg.SteamPath,
        cfg.SteamUserId,
        appName: StartSdAppName,
        exe: StartSdExePath,
        startDir: AppContext.BaseDirectory,
        launchOptions: Quote(scriptPath),
        icon: iconPath,
        out var message);

    SaveRuntimeEnvironment(configSvc, cfg, appId);

    if (ok)
    {
        Console.WriteLine($"SD CARD shortcut configured. {message}");
        ApplyStartSdArtwork(cfg, appId);
    }
    else
        Console.Error.WriteLine($"SD CARD shortcut: {message}");
}

void ApplyStartSdArtwork(AppConfig cfg, uint appId)
{
    var gridDir = Path.Combine(cfg.SteamPath, "userdata", cfg.SteamUserId, "config", "grid");
    Directory.CreateDirectory(gridDir);

    var portraitSrc = ResolveOutputFilePath(Path.Combine("assets", "start-sd-capsule.png"));
    var horizontalSrc = ResolveOutputFilePath(Path.Combine("assets", "start-sd-horizontal.png"));
    var heroSrc = ResolveOutputFilePath(Path.Combine("assets", "start-sd-hero.png"));
    var logoSrc = ResolveOutputFilePath(Path.Combine("assets", "start-sd-logo.png"));

    var copied = 0;
    copied += CopyIfExists(portraitSrc, Path.Combine(gridDir, $"{appId}p.png"));
    copied += CopyIfExists(horizontalSrc, Path.Combine(gridDir, $"{appId}.png"));
    copied += CopyIfExists(heroSrc, Path.Combine(gridDir, $"{appId}_hero.png"));
    copied += CopyIfExists(logoSrc, Path.Combine(gridDir, $"{appId}_logo.png"));

    if (copied > 0)
        Console.WriteLine($"Start SD artwork updated in Steam grid with AppId {appId}.");
    else
        Console.WriteLine("Start SD artwork: no files found to copy.");
}

int CopyIfExists(string? source, string destination)
{
    if (string.IsNullOrWhiteSpace(source) || !File.Exists(source))
        return 0;

    File.Copy(source, destination, overwrite: true);
    return 1;
}

string? ResolveOutputFilePath(params string[] candidates)
{
    foreach (var candidate in candidates)
    {
        var fullPath = Path.GetFullPath(Path.Combine(AppContext.BaseDirectory, candidate));
        if (File.Exists(fullPath))
            return fullPath;
    }

    return null;
}

string Quote(string value) => $"\"{value}\"";

int RunUsers(ConfigService configSvc)
{
    var cfg = configSvc.TryLoad();
    var steamRoot = cfg?.SteamPath;

    if (string.IsNullOrWhiteSpace(steamRoot) || !Directory.Exists(steamRoot))
        steamRoot = SteamPaths.FindSteamRoot();

    if (string.IsNullOrWhiteSpace(steamRoot))
    {
        Console.Error.WriteLine("Steam not found.");
        return 2;
    }

    var users = SteamPaths.ListUsers(steamRoot);
    if (users.Count == 0)
    {
        Console.WriteLine("No Steam users found.");
        return 0;
    }

    foreach (var user in users)
    {
        var marker = cfg?.SteamUserId == user.UserId ? "*" : " ";
        Console.WriteLine($"{marker} {user.PersonaName} ({user.UserId})");
    }

    return 0;
}

int RunConfigure(string[] argv, ConfigService configSvc)
{
    var userId = GetOption(argv, "--user");
    if (string.IsNullOrWhiteSpace(userId))
    {
        Console.Error.WriteLine("Usage: insertplay configure --user <steamUserId>");
        return 1;
    }

    var cfg = configSvc.TryLoad();
    var steamRoot = cfg?.SteamPath;
    if (string.IsNullOrWhiteSpace(steamRoot) || !Directory.Exists(steamRoot))
        steamRoot = SteamPaths.FindSteamRoot();

    if (string.IsNullOrWhiteSpace(steamRoot))
    {
        Console.Error.WriteLine("Steam not found.");
        return 2;
    }

    var users = SteamPaths.ListUsers(steamRoot);
    if (!users.Any(u => u.UserId == userId))
    {
        Console.Error.WriteLine("The specified user was not found in userdata.");
        return 3;
    }

    cfg ??= new AppConfig();
    cfg.SteamPath = steamRoot;
    cfg.SteamUserId = userId;
    cfg.SchemaVersion = 1;
    configSvc.Save(cfg);
    SaveRuntimeEnvironment(configSvc, cfg, AppIdCalculator.Calculate(StartSdExePath, StartSdAppName));

    Console.WriteLine("Configuration updated.");
    return 0;
}

void SaveRuntimeEnvironment(ConfigService configSvc, AppConfig cfg, uint appId)
{
    var gameId = GameIdCalculator.Calculate(appId);
    var variables = new Dictionary<string, string>
    {
        ["INSERTPLAY_SD_APP_ID"] = appId.ToString(),
        ["INSERTPLAY_SD_GAME_ID"] = gameId.ToString(),
        ["INSERTPLAY_STEAM_PATH"] = cfg.SteamPath,
        ["INSERTPLAY_STEAM_USER_ID"] = cfg.SteamUserId
    };

    configSvc.SaveRuntimeEnvironment(variables);
    Console.WriteLine($"Runtime environment saved to {configSvc.GetRuntimeEnvPath()}");
}

int RunShortcut(string[] argv, ConfigService configSvc, ShortcutService service)
{
    if (argv.Length < 2)
    {
        Console.Error.WriteLine("Usage: insertplay shortcut <add|update|remove|list> [options]");
        return 1;
    }

    var cfg = configSvc.TryLoad();
    if (!configSvc.IsValid(cfg, out var configError))
    {
        Console.Error.WriteLine(configError);
        return 1;
    }

    var action = argv[1];
    return action switch
    {
        "add" or "update" => ShortcutUpsert(argv, cfg!, service),
        "remove" => ShortcutRemove(argv, cfg!, service),
        "list" => ShortcutList(cfg!, service),
        _ => 1
    };
}

int RunRuntime(string[] argv, ConfigService configSvc, ShortcutService service, ShortcutIdentityService identityService)
{
    if (argv.Length < 2)
    {
        Console.Error.WriteLine("Usage: insertplay runtime <install-shortcut|autorun-launch|ensure-identity> [options]");
        return 1;
    }

    return argv[1] switch
    {
        "install-shortcut" => RuntimeInstallShortcut(argv, configSvc, service, identityService),
        "autorun-launch" => RuntimeAutorunLaunch(argv, configSvc, service),
        "ensure-identity" => RuntimeEnsureIdentity(argv, identityService),
        _ => 1
    };
}

int RunSteam(string[] argv, ConfigService configSvc)
{
    if (argv.Length < 2)
    {
        Console.Error.WriteLine("Usage: insertplay steam restart");
        return 1;
    }

    return argv[1] switch
    {
        "restart" => SteamRestart(configSvc),
        _ => 1
    };
}

int SteamRestart(ConfigService configSvc)
{
    var cfg = configSvc.TryLoad();
    var steamPath = cfg?.SteamPath;

    if (string.IsNullOrWhiteSpace(steamPath) || !Directory.Exists(steamPath))
        steamPath = SteamPaths.FindSteamRoot();

    if (string.IsNullOrWhiteSpace(steamPath) || !Directory.Exists(steamPath))
    {
        Console.Error.WriteLine("Steam path not found. Run 'insertplay setup' first or ensure Steam is installed.");
        return 2;
    }

    if (!TryRestartSteam(steamPath, out var message))
    {
        Console.Error.WriteLine($"Steam restart failed: {message}");
        return 20;
    }

    Console.WriteLine(message);
    return 0;
}

int RuntimeInstallShortcut(string[] argv, ConfigService configSvc, ShortcutService service, ShortcutIdentityService identityService)
{
    try
    {
        var cfg = configSvc.TryLoad();
        if (!configSvc.IsValid(cfg, out _))
        {
            Console.Error.WriteLine("InsertPlay runtime configuration not found. Run 'insertplay setup' first.");
            return 10;
        }

        var sdRootRaw = GetOption(argv, "--sd-root");
        var restartSteam = HasFlag(argv, "--restart-steam");
        if (string.IsNullOrWhiteSpace(sdRootRaw))
        {
            Console.Error.WriteLine("Usage: insertplay runtime install-shortcut --sd-root <path> [--restart-steam]");
            return 1;
        }

        var sdRoot = Path.GetFullPath(sdRootRaw);
        if (!Directory.Exists(sdRoot))
        {
            Console.Error.WriteLine($"SD root not found: {sdRoot}");
            return 1;
        }

        var manifestPath = ResolveManifestPath(sdRoot);
        if (string.IsNullOrWhiteSpace(manifestPath))
        {
            Console.Error.WriteLine($"Manifest not found. Expected one of: {Path.Combine(sdRoot, "steam-shortcut.json")} or {Path.Combine(sdRoot, "insertplay", "steam-shortcut.json")}");
            return 11;
        }

        if (!TryReadManifest(manifestPath, out var manifest, out var manifestError))
        {
            Console.Error.WriteLine(manifestError);
            return 12;
        }

        var shortcut = manifest!.Shortcut!;
        var launch = shortcut.Launch!;
        var verification = shortcut.LaunchVerification!;

        var appName = shortcut.AppName.Trim();
        var exe = ResolveSdPlaceholder(launch.Exe, sdRoot);
        var argsResolved = ResolveSdPlaceholder(launch.Args, sdRoot);
        var startDir = ResolveSdPlaceholder(launch.StartDir, sdRoot);

        if (!identityService.TryGetOrCreate(sdRoot, appName, shortcut.Guid, out var shortcutGuid, out var identityError))
        {
            Console.Error.WriteLine(identityError);
            return 12;
        }

        var iconPath = ResolveArtworkPath(
            sdRoot,
            shortcut.Artwork?.Icon,
            "steam-artwork/icon.png",
            "insertplay/steam-artwork/icon.png");

        var ok = service.AddOrUpdate(
            cfg!.SteamPath,
            cfg.SteamUserId,
            appName,
            exe,
            startDir,
            argsResolved,
            iconPath,
            isHidden: true,
            out var message);

        if (!ok)
        {
            Console.Error.WriteLine(message);
            return 13;
        }

        var appId = AppIdCalculator.Calculate(exe, appName);
        var gameId = GameIdCalculator.Calculate(appId);

        ApplyShortcutArtwork(
            cfg,
            appId,
            ResolveArtworkPath(sdRoot, shortcut.Artwork?.Capsule, "steam-artwork/capsule.png", "insertplay/steam-artwork/capsule.png"),
            ResolveArtworkPath(sdRoot, shortcut.Artwork?.Horizontal, "steam-artwork/horizontal.png", "insertplay/steam-artwork/horizontal.png"),
            ResolveArtworkPath(sdRoot, shortcut.Artwork?.Hero, "steam-artwork/hero.png", "insertplay/steam-artwork/hero.png"),
            ResolveArtworkPath(sdRoot, shortcut.Artwork?.Logo, "steam-artwork/logo.png", "insertplay/steam-artwork/logo.png"));

        var runtimeShortcutEnvPath = configSvc.SaveRuntimeShortcutEnvironment(shortcutGuid, new Dictionary<string, string>
        {
            ["INSERTPLAY_SHORTCUT_GUID"] = shortcutGuid,
            ["INSERTPLAY_SPECIFIC_GAME_ID"] = gameId.ToString(),
            ["INSERTPLAY_SPECIFIC_PROCESS_NAME"] = verification.ProcessName.Trim(),
            ["INSERTPLAY_SPECIFIC_TIMEOUT_SECONDS"] = verification.TimeoutSeconds.ToString()
        });

        Console.WriteLine($"Specific shortcut installed with GUID {shortcutGuid}, AppId {appId} and GameId {gameId}.");
        Console.WriteLine($"Runtime shortcut environment saved to {runtimeShortcutEnvPath}");

        if (restartSteam)
        {
            if (TryRestartSteam(cfg.SteamPath, out var restartMessage))
                Console.WriteLine(restartMessage);
            else
                Console.Error.WriteLine($"Steam restart failed: {restartMessage}");
        }

        return 0;
    }
    catch (Exception ex)
    {
        Console.Error.WriteLine($"Unexpected runtime failure: {ex.Message}");
        return 15;
    }
}

int RuntimeAutorunLaunch(string[] argv, ConfigService configSvc, ShortcutService service)
{
    var sdRootRaw = GetOption(argv, "--sd-root");
    if (string.IsNullOrWhiteSpace(sdRootRaw))
    {
        Console.Error.WriteLine("Usage: insertplay runtime autorun-launch --sd-root <path> [--guid <guid>]");
        return 1;
    }

    var sdRoot = Path.GetFullPath(sdRootRaw);
    if (!Directory.Exists(sdRoot))
    {
        Console.Error.WriteLine($"SD root not found: {sdRoot}");
        return 1;
    }

    var genericRuntimeEnvPath = configSvc.GetRuntimeEnvPath();
    if (!TryReadEnvironmentFile(genericRuntimeEnvPath, out var runtimeVars, out var runtimeEnvError))
    {
        Console.Error.WriteLine(runtimeEnvError);
        return 10;
    }

    var genericGameId = GetEnvValue(runtimeVars, "INSERTPLAY_SD_GAME_ID");
    if (string.IsNullOrWhiteSpace(genericGameId))
    {
        Console.Error.WriteLine("InsertPlay runtime configuration not found. Run 'insertplay setup' first.");
        return 10;
    }

    var explicitGuid = GetOption(argv, "--guid");
    var shortcutGuid = ResolveShortcutGuid(explicitGuid, sdRoot, out var shortcutGuidMessage);
    if (!string.IsNullOrWhiteSpace(shortcutGuidMessage))
        Console.WriteLine($"[insertplay runtime] {shortcutGuidMessage}");

    var cfg = configSvc.TryLoad();
    var hasValidConfig = configSvc.IsValid(cfg, out _);
    var specificLaunchError = string.Empty;

    if (!string.IsNullOrWhiteSpace(shortcutGuid)
        && TryReadSpecificRuntimeLaunch(
            configSvc,
            shortcutGuid,
            out var specificGameId,
            out var specificProcessName,
            out var specificTimeoutSeconds,
            out specificLaunchError))
    {
        var specificExists = true;
        if (hasValidConfig)
            specificExists = SpecificShortcutExistsInSteam(cfg!, service, specificGameId);

        if (!specificExists)
        {
            Console.WriteLine($"[insertplay runtime] Specific shortcut guid={shortcutGuid} not found in Steam shortcuts. Falling back to generic shortcut.");
        }
        else
        {
            Console.WriteLine($"[insertplay runtime] Trying specific shortcut guid={shortcutGuid} gameId={specificGameId}");
            if (TryLaunchSteamGame(specificGameId, out var specificLaunchMessage))
            {
                if (WaitForProcessPattern(specificProcessName, specificTimeoutSeconds))
                {
                    Console.WriteLine("[insertplay runtime] Specific launch succeeded.");
                    return 0;
                }

                Console.WriteLine("[insertplay runtime] Specific process was not detected in time. Falling back to generic shortcut.");
            }
            else
            {
                Console.WriteLine($"[insertplay runtime] Failed to request specific launch: {specificLaunchMessage}. Falling back to generic shortcut.");
            }
        }
    }
    else if (!string.IsNullOrWhiteSpace(specificLaunchError))
    {
        Console.WriteLine($"[insertplay runtime] {specificLaunchError} Falling back to generic shortcut.");
    }
    else
    {
        Console.WriteLine("[insertplay runtime] Specific runtime shortcut env not available. Falling back to generic shortcut.");
    }

    Console.WriteLine($"[insertplay runtime] Starting generic shortcut gameId={genericGameId}");
    if (TryLaunchSteamGame(genericGameId, out var genericLaunchMessage))
        return 0;

    Console.Error.WriteLine($"Failed to launch generic shortcut: {genericLaunchMessage}");
    return 16;
}

int RuntimeEnsureIdentity(string[] argv, ShortcutIdentityService identityService)
{
    var sdRootRaw = GetOption(argv, "--sd-root");
    if (string.IsNullOrWhiteSpace(sdRootRaw))
    {
        Console.Error.WriteLine("Usage: insertplay runtime ensure-identity --sd-root <path>");
        return 1;
    }

    var sdRoot = Path.GetFullPath(sdRootRaw);
    if (!Directory.Exists(sdRoot))
    {
        Console.Error.WriteLine($"SD root not found: {sdRoot}");
        return 1;
    }

    var appName = "InsertPlay Game";
    string? manifestGuid = null;
    var manifestPath = ResolveManifestPath(sdRoot);
    if (!string.IsNullOrWhiteSpace(manifestPath)
        && TryReadManifest(manifestPath, out var manifest, out _)
        && manifest?.Shortcut is not null)
    {
        appName = string.IsNullOrWhiteSpace(manifest.Shortcut.AppName)
            ? appName
            : manifest.Shortcut.AppName.Trim();
        manifestGuid = string.IsNullOrWhiteSpace(manifest.Shortcut.Guid)
            ? null
            : manifest.Shortcut.Guid.Trim();
    }

    if (!identityService.TryGetOrCreate(sdRoot, appName, manifestGuid, out var shortcutGuid, out var identityError))
    {
        Console.Error.WriteLine(identityError);
        return 12;
    }

    var identityPath = Path.Combine(sdRoot, "shortcut-identity.json");
    Console.WriteLine($"Shortcut identity ensured with GUID {shortcutGuid} at {identityPath}");
    return 0;
}

bool TryReadManifest(string manifestPath, out SteamShortcutManifest? manifest, out string error)
{
    manifest = null;
    error = string.Empty;

    try
    {
        var json = File.ReadAllText(manifestPath);
        manifest = JsonSerializer.Deserialize<SteamShortcutManifest>(json, new JsonSerializerOptions
        {
            PropertyNameCaseInsensitive = true
        });
    }
    catch (Exception ex)
    {
        error = $"Failed to parse manifest: {ex.Message}";
        return false;
    }

    if (manifest is null)
    {
        error = "Manifest is empty.";
        return false;
    }

    if (manifest.SchemaVersion != 1)
    {
        error = $"Unsupported manifest schemaVersion: {manifest.SchemaVersion}";
        return false;
    }

    if (manifest.Shortcut is null)
    {
        error = "Manifest field 'shortcut' is required.";
        return false;
    }

    if (string.IsNullOrWhiteSpace(manifest.Shortcut.AppName))
    {
        error = "Manifest field 'shortcut.appName' is required.";
        return false;
    }

    if (!string.IsNullOrWhiteSpace(manifest.Shortcut.Guid)
        && !Guid.TryParse(manifest.Shortcut.Guid, out _))
    {
        error = "Manifest field 'shortcut.guid' must be a valid UUID/GUID when provided.";
        return false;
    }

    if (manifest.Shortcut.Launch is null)
    {
        error = "Manifest field 'shortcut.launch' is required.";
        return false;
    }

    if (string.IsNullOrWhiteSpace(manifest.Shortcut.Launch.Exe))
    {
        error = "Manifest field 'shortcut.launch.exe' is required.";
        return false;
    }

    if (string.IsNullOrWhiteSpace(manifest.Shortcut.Launch.Args))
    {
        error = "Manifest field 'shortcut.launch.args' is required.";
        return false;
    }

    if (string.IsNullOrWhiteSpace(manifest.Shortcut.Launch.StartDir))
    {
        error = "Manifest field 'shortcut.launch.startDir' is required.";
        return false;
    }

    if (manifest.Shortcut.LaunchVerification is null)
    {
        error = "Manifest field 'shortcut.launchVerification' is required.";
        return false;
    }

    if (string.IsNullOrWhiteSpace(manifest.Shortcut.LaunchVerification.ProcessName))
    {
        error = "Manifest field 'shortcut.launchVerification.processName' is required.";
        return false;
    }

    if (manifest.Shortcut.LaunchVerification.TimeoutSeconds <= 0)
    {
        error = "Manifest field 'shortcut.launchVerification.timeoutSeconds' must be a positive integer.";
        return false;
    }

    return true;
}

void ApplyShortcutArtwork(AppConfig cfg, uint appId, string? capsuleSrc, string? horizontalSrc, string? heroSrc, string? logoSrc)
{
    var gridDir = Path.Combine(cfg.SteamPath, "userdata", cfg.SteamUserId, "config", "grid");
    Directory.CreateDirectory(gridDir);

    var copied = 0;
    copied += CopyIfExists(capsuleSrc, Path.Combine(gridDir, $"{appId}p.png"));
    copied += CopyIfExists(horizontalSrc, Path.Combine(gridDir, $"{appId}.png"));
    copied += CopyIfExists(heroSrc, Path.Combine(gridDir, $"{appId}_hero.png"));
    copied += CopyIfExists(logoSrc, Path.Combine(gridDir, $"{appId}_logo.png"));

    if (copied > 0)
        Console.WriteLine($"Specific shortcut artwork updated in Steam grid with AppId {appId}.");
    else
        Console.WriteLine("Specific shortcut artwork: no files found to copy.");
}

string ResolveSdPlaceholder(string value, string sdRoot)
{
    return value.Replace("{SD_ROOT}", sdRoot, StringComparison.Ordinal);
}

string? ResolveManifestPath(string sdRoot)
{
    var rootPath = Path.Combine(sdRoot, "steam-shortcut.json");
    if (File.Exists(rootPath))
        return rootPath;

    var legacyPath = Path.Combine(sdRoot, "insertplay", "steam-shortcut.json");
    if (File.Exists(legacyPath))
        return legacyPath;

    return null;
}

string ResolveArtworkPath(string sdRoot, string? value, params string[] fallbackRelativePaths)
{
    if (!string.IsNullOrWhiteSpace(value))
    {
        var replaced = ResolveSdPlaceholder(value, sdRoot).Trim();
        var absolute = Path.IsPathRooted(replaced)
            ? replaced
            : Path.GetFullPath(Path.Combine(sdRoot, replaced));

        if (File.Exists(absolute))
            return absolute;
    }

    foreach (var fallbackRelativePath in fallbackRelativePaths)
    {
        if (string.IsNullOrWhiteSpace(fallbackRelativePath))
            continue;

        var absolute = Path.GetFullPath(Path.Combine(sdRoot, fallbackRelativePath));
        if (File.Exists(absolute))
            return absolute;
    }

    return string.Empty;
}

string? ResolveShortcutGuid(string? explicitGuid, string sdRoot, out string message)
{
    message = string.Empty;

    if (!string.IsNullOrWhiteSpace(explicitGuid))
    {
        if (Guid.TryParse(explicitGuid, out var parsedGuid))
            return parsedGuid.ToString("D");

        message = $"Provided shortcut GUID is invalid: {explicitGuid}.";
        return null;
    }

    var identityPaths = new[]
    {
        Path.Combine(sdRoot, "shortcut-identity.json"),
        Path.Combine(sdRoot, "insertplay", "shortcut-identity.json")
    };

    foreach (var identityPath in identityPaths)
    {
        if (!File.Exists(identityPath))
            continue;

        if (!TryReadShortcutGuidFromIdentity(identityPath, out var identityGuid, out var identityError))
        {
            message = $"Failed to read shortcut identity file '{identityPath}': {identityError}";
            return null;
        }

        return identityGuid;
    }

    return null;
}

bool TryReadShortcutGuidFromIdentity(string identityPath, out string shortcutGuid, out string error)
{
    shortcutGuid = string.Empty;
    error = string.Empty;

    try
    {
        using var document = JsonDocument.Parse(File.ReadAllText(identityPath));
        if (!document.RootElement.TryGetProperty("guid", out var guidElement))
        {
            error = "guid field is missing.";
            return false;
        }

        var rawGuid = guidElement.GetString();
        if (string.IsNullOrWhiteSpace(rawGuid) || !Guid.TryParse(rawGuid, out var parsedGuid))
        {
            error = "guid field is invalid.";
            return false;
        }

        shortcutGuid = parsedGuid.ToString("D");
        return true;
    }
    catch (Exception ex)
    {
        error = ex.Message;
        return false;
    }
}

bool TryReadSpecificRuntimeLaunch(
    ConfigService configSvc,
    string shortcutGuid,
    out string gameId,
    out string processName,
    out int timeoutSeconds,
    out string error)
{
    gameId = string.Empty;
    processName = string.Empty;
    timeoutSeconds = 0;
    error = string.Empty;

    string path;
    try
    {
        path = configSvc.GetRuntimeShortcutEnvPath(shortcutGuid);
    }
    catch (ArgumentException ex)
    {
        error = ex.Message;
        return false;
    }

    if (!TryReadEnvironmentFile(path, out var values, out var envError))
    {
        error = envError;
        return false;
    }

    gameId = GetEnvValue(values, "INSERTPLAY_SPECIFIC_GAME_ID");
    processName = GetEnvValue(values, "INSERTPLAY_SPECIFIC_PROCESS_NAME");
    var timeoutRaw = GetEnvValue(values, "INSERTPLAY_SPECIFIC_TIMEOUT_SECONDS");

    if (string.IsNullOrWhiteSpace(gameId)
        || string.IsNullOrWhiteSpace(processName)
        || !int.TryParse(timeoutRaw, out timeoutSeconds)
        || timeoutSeconds <= 0)
    {
        error = $"Specific runtime shortcut data is incomplete for guid={shortcutGuid}.";
        return false;
    }

    return true;
}

bool SpecificShortcutExistsInSteam(AppConfig cfg, ShortcutService service, string specificGameId)
{
    if (!ulong.TryParse(specificGameId, out var parsedGameId))
        return false;

    var appId = (uint)(parsedGameId >> 32);
    var items = service.List(cfg.SteamPath, cfg.SteamUserId);
    return items.Any(item => item.AppId == appId);
}

bool TryLaunchSteamGame(string gameId, out string message)
{
    if (!ulong.TryParse(gameId, out _))
    {
        message = "Game ID is invalid.";
        return false;
    }

    var uri = $"steam://rungameid/{gameId}";
    if (TryRunProcess("steam", uri, waitForExit: false, timeoutMs: 0))
    {
        message = "launch requested via steam command";
        return true;
    }

    if (TryRunProcess("xdg-open", uri, waitForExit: false, timeoutMs: 0))
    {
        message = "launch requested via xdg-open";
        return true;
    }

    message = "steam command and xdg-open both failed";
    return false;
}

bool WaitForProcessPattern(string processPattern, int timeoutSeconds)
{
    for (var i = 0; i < timeoutSeconds; i++)
    {
        if (IsProcessRunningPattern(processPattern))
            return true;

        Thread.Sleep(1000);
    }

    return false;
}

bool IsProcessRunningPattern(string processPattern)
{
    if (string.IsNullOrWhiteSpace(processPattern))
        return false;

    var escapedPattern = processPattern.Replace("'", "'\"'\"'");
    var shellCommand = $"pgrep -f -- '{escapedPattern}' >/dev/null 2>&1";
    var shellArgs = $"-lc \"{shellCommand}\"";

    if (!TryRunProcessAndGetExitCode("/bin/bash", shellArgs, timeoutMs: 3000, out var exitCode))
        return false;

    return exitCode == 0;
}

bool TryRunProcessAndGetExitCode(string fileName, string arguments, int timeoutMs, out int exitCode)
{
    exitCode = -1;

    try
    {
        var process = Process.Start(new ProcessStartInfo
        {
            FileName = fileName,
            Arguments = arguments,
            UseShellExecute = false,
            RedirectStandardOutput = true,
            RedirectStandardError = true,
            CreateNoWindow = true
        });

        if (process is null)
            return false;

        if (!process.WaitForExit(timeoutMs))
            return false;

        exitCode = process.ExitCode;
        return true;
    }
    catch
    {
        return false;
    }
}

bool TryReadEnvironmentFile(string path, out Dictionary<string, string> variables, out string error)
{
    variables = new Dictionary<string, string>(StringComparer.Ordinal);
    error = string.Empty;

    if (!File.Exists(path))
    {
        error = $"Environment file not found: {path}";
        return false;
    }

    try
    {
        foreach (var rawLine in File.ReadAllLines(path))
        {
            var line = rawLine.Trim();
            if (string.IsNullOrWhiteSpace(line) || line.StartsWith('#'))
                continue;

            if (line.StartsWith("export ", StringComparison.Ordinal))
                line = line[7..].Trim();

            var separator = line.IndexOf('=');
            if (separator <= 0)
                continue;

            var key = line[..separator].Trim();
            var value = line[(separator + 1)..].Trim();

            variables[key] = ParseEnvironmentValue(value);
        }

        return true;
    }
    catch (Exception ex)
    {
        error = $"Failed to read environment file '{path}': {ex.Message}";
        return false;
    }
}

string ParseEnvironmentValue(string value)
{
    if (value.Length >= 2 && value[0] == '\'' && value[^1] == '\'')
    {
        var inner = value[1..^1];
        return inner.Replace("'\"'\"'", "'");
    }

    if (value.Length >= 2 && value[0] == '"' && value[^1] == '"')
    {
        var inner = value[1..^1];
        return inner.Replace("\\\"", "\"").Replace("\\\\", "\\");
    }

    return value;
}

string GetEnvValue(IReadOnlyDictionary<string, string> vars, string key)
{
    return vars.TryGetValue(key, out var value) ? value : string.Empty;
}

int ShortcutUpsert(string[] argv, AppConfig cfg, ShortcutService service)
{
    var name = GetOption(argv, "--name");
    var exe = GetOption(argv, "--exe");
    var startDir = GetOption(argv, "--startdir") ?? string.Empty;
    var launchOptions = GetOption(argv, "--args") ?? string.Empty;
    var icon = GetOption(argv, "--icon") ?? string.Empty;
    var hiddenRaw = GetOption(argv, "--hidden");
    var isHidden = false;

    if (!string.IsNullOrWhiteSpace(hiddenRaw) && !bool.TryParse(hiddenRaw, out isHidden))
    {
        Console.Error.WriteLine("Invalid value for --hidden. Use true or false.");
        return 1;
    }

    if (string.IsNullOrWhiteSpace(name) || string.IsNullOrWhiteSpace(exe))
    {
        Console.Error.WriteLine("Usage: insertplay shortcut add --name <name> --exe <path> [--startdir <dir>] [--args <args>] [--icon <icon>] [--hidden <true|false>]");
        return 1;
    }

    var ok = service.AddOrUpdate(
        cfg.SteamPath,
        cfg.SteamUserId,
        name,
        exe,
        startDir,
        launchOptions,
        icon,
        isHidden,
        out var message);
    Console.WriteLine(message);
    return ok ? 0 : 5;
}

int ShortcutRemove(string[] argv, AppConfig cfg, ShortcutService service)
{
    var name = GetOption(argv, "--name");
    if (string.IsNullOrWhiteSpace(name))
    {
        Console.Error.WriteLine("Usage: insertplay shortcut remove --name <name>");
        return 1;
    }

    var ok = service.Remove(cfg.SteamPath, cfg.SteamUserId, name, out var message);
    Console.WriteLine(message);
    return ok ? 0 : 6;
}

int ShortcutList(AppConfig cfg, ShortcutService service)
{
    var items = service.List(cfg.SteamPath, cfg.SteamUserId)
        .OrderBy(i => i.AppName, StringComparer.OrdinalIgnoreCase)
        .ToList();

    if (items.Count == 0)
    {
        Console.WriteLine("No shortcuts found.");
        return 0;
    }

    foreach (var item in items)
        Console.WriteLine($"{item.AppName} | appId={item.AppId} | exe={item.Exe}");

    return 0;
}

string? GetOption(string[] argv, string key)
{
    for (var i = 0; i < argv.Length - 1; i++)
    {
        if (argv[i].Equals(key, StringComparison.OrdinalIgnoreCase))
            return argv[i + 1];
    }

    return null;
}

bool HasFlag(string[] argv, string key)
{
    return argv.Any(arg => arg.Equals(key, StringComparison.OrdinalIgnoreCase));
}

bool TryRestartSteam(string steamPath, out string message)
{
    if (OperatingSystem.IsLinux())
    {
        TryRunProcess("steam", "-shutdown", waitForExit: true, timeoutMs: 10000);
        WaitForSteamToStop(timeoutMs: 15000);

        var steamSh = Path.Combine(steamPath, "steam.sh");
        if (File.Exists(steamSh) && TryLaunchSteamLinux($"\"{steamSh}\""))
        {
            message = "Steam restart requested (steam.sh).";
            return true;
        }

        if (TryLaunchSteamLinux("steam"))
        {
            message = "Steam restart requested (steam command).";
            return true;
        }

        if (TryLaunchSteamLinux("flatpak run com.valvesoftware.Steam"))
        {
            message = "Steam restart requested (flatpak).";
            return true;
        }

        if (TryLaunchSteamLinux("xdg-open steam://open/main"))
        {
            message = "Steam restart requested (steam URI).";
            return true;
        }

        message = "Could not start Steam process automatically.";
        return false;
    }

    if (OperatingSystem.IsWindows())
    {
        TryRunProcess("steam", "-shutdown", waitForExit: true, timeoutMs: 10000);
        Thread.Sleep(1500);

        var steamExe = Path.Combine(steamPath, "steam.exe");
        if (File.Exists(steamExe) && TryRunProcess(steamExe, string.Empty, waitForExit: false, timeoutMs: 0))
        {
            message = "Steam restart requested (steam.exe).";
            return true;
        }

        if (TryRunProcess("steam", string.Empty, waitForExit: false, timeoutMs: 0))
        {
            message = "Steam restart requested (steam command).";
            return true;
        }

        message = "Could not start Steam process automatically.";
        return false;
    }

    message = "Steam restart is not supported on this operating system.";
    return false;
}

bool TryRunProcess(string fileName, string arguments, bool waitForExit, int timeoutMs)
{
    try
    {
        var process = Process.Start(new ProcessStartInfo
        {
            FileName = fileName,
            Arguments = arguments,
            UseShellExecute = false,
            RedirectStandardOutput = true,
            RedirectStandardError = true,
            CreateNoWindow = true
        });

        if (process is null)
            return false;

        if (waitForExit)
        {
            if (!process.WaitForExit(timeoutMs))
                return false;
        }

        return true;
    }
    catch
    {
        return false;
    }
}

bool TryLaunchSteamLinux(string launchCommand)
{
    var escapedCommand = launchCommand.Replace("\"", "\\\"");
    var shellArgs = $"-lc \"nohup {escapedCommand} >/dev/null 2>&1 &\"";

    if (!TryRunProcess("/bin/bash", shellArgs, waitForExit: true, timeoutMs: 5000))
        return false;

    return WaitForSteamToStart(timeoutMs: 15000);
}

bool WaitForSteamToStart(int timeoutMs)
{
    var startedAt = Environment.TickCount64;
    while (Environment.TickCount64 - startedAt < timeoutMs)
    {
        if (IsSteamProcessRunning())
            return true;

        Thread.Sleep(500);
    }

    return false;
}

bool WaitForSteamToStop(int timeoutMs)
{
    var startedAt = Environment.TickCount64;
    while (Environment.TickCount64 - startedAt < timeoutMs)
    {
        if (!IsSteamProcessRunning())
            return true;

        Thread.Sleep(500);
    }

    return false;
}

bool IsSteamProcessRunning()
{
    try
    {
        return Process.GetProcessesByName("steam").Length > 0;
    }
    catch
    {
        return false;
    }
}

void PrintHelp()
{
    Console.WriteLine("InsertPlay CLI (MVP)");
    Console.WriteLine("Commands:");
    Console.WriteLine("  setup");
    Console.WriteLine("  users");
    Console.WriteLine("  configure --user <steamUserId>");
    Console.WriteLine("  shortcut add --name <name> --exe <path> [--startdir <dir>] [--args <args>] [--icon <path>] [--hidden <true|false>]");
    Console.WriteLine("  shortcut update --name <name> --exe <path> [--startdir <dir>] [--args <args>] [--icon <path>] [--hidden <true|false>]");
    Console.WriteLine("  shortcut remove --name <name>");
    Console.WriteLine("  shortcut list");
    Console.WriteLine("  steam restart");
    Console.WriteLine("  runtime install-shortcut --sd-root <path> [--restart-steam]");
    Console.WriteLine("  runtime autorun-launch --sd-root <path> [--guid <guid>]");
    Console.WriteLine("  runtime ensure-identity --sd-root <path>");
}

int PrintAndExit(int code)
{
    PrintHelp();
    return code;
}
