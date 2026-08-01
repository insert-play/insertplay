using InsertPlay.Cli.Config;
using InsertPlay.Cli.Models;
using InsertPlay.Cli.Steam;

var configService = new ConfigService();
var shortcutService = new ShortcutService();
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

    var scriptPath = ResolveOutputFilePath(
        "insertplay-start-bootstrap.sh",
        Path.Combine("assets", "runtime", "insertplay-start-bootstrap.sh"),
        Path.Combine("steam-shortcuts", "insertplay-start-bootstrap.sh"));

    if (scriptPath is null)
    {
        Console.Error.WriteLine("SD CARD shortcut: local script not found in the execution directory.");
        return;
    }

    var iconPath = ResolveOutputFilePath(
        "start-sd-icon.png",
        Path.Combine("assets", "steam-shortcuts", "start-sd-icon.png"),
        Path.Combine("steam-shortcuts", "start-sd-icon.png")) ?? string.Empty;

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

    var portraitSrc = ResolveOutputFilePath(
        "start-sd-capsule.png",
        Path.Combine("assets", "steam-shortcuts", "start-sd-capsule.png"),
        Path.Combine("steam-shortcuts", "start-sd-capsule.png"));

    var horizontalSrc = ResolveOutputFilePath(
        "start-sd-horizontal.png",
        Path.Combine("assets", "steam-shortcuts", "start-sd-horizontal.png"),
        Path.Combine("steam-shortcuts", "start-sd-horizontal.png"));

    var heroSrc = ResolveOutputFilePath(
        "start-sd-hero.png",
        Path.Combine("assets", "steam-shortcuts", "start-sd-hero.png"),
        Path.Combine("steam-shortcuts", "start-sd-hero.png"));

    var logoSrc = ResolveOutputFilePath(
        "start-sd-logo.png",
        Path.Combine("assets", "steam-shortcuts", "start-sd-logo.png"),
        Path.Combine("steam-shortcuts", "start-sd-logo.png"));

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
}

int PrintAndExit(int code)
{
    PrintHelp();
    return code;
}
