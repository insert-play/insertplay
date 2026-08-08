using InsertPlay.Cli.Application.Abstractions;
using InsertPlay.Cli.Application.Common;
using InsertPlay.Cli.Application.Services;
using InsertPlay.Cli.Domain.Configuration;
using InsertPlay.Cli.Domain.Steam;

namespace InsertPlay.Cli.Application.UseCases.Setup;

public sealed record SteamUsersLookupResult(
    bool Success,
    string? SteamRoot,
    IReadOnlyList<SteamUserInfo> Users,
    string? Error,
    int ExitCode)
{
    public static SteamUsersLookupResult Ok(string steamRoot, IReadOnlyList<SteamUserInfo> users) =>
        new(true, steamRoot, users, null, 0);

    public static SteamUsersLookupResult Fail(string error, int exitCode) =>
        new(false, null, [], error, exitCode);
}

public sealed record SdShortcutBootstrapResult(IReadOnlyList<string> InfoLines, string? ErrorMessage)
{
    public static SdShortcutBootstrapResult Info(params string[] lines) => new(lines, null);

    public static SdShortcutBootstrapResult Error(string message) => new([], message);
}

public sealed record SetupCompletionResult(string ConfigPath, SdShortcutBootstrapResult ShortcutBootstrap);

public sealed class SetupUseCase(
    ISteamInstallationLocator steamLocator,
    IConfigStore configStore,
    IShortcutRepository shortcutRepository,
    IBundledAssetLocator assetLocator,
    RuntimeEnvironmentWriter runtimeEnvironmentWriter,
    SteamArtworkInstaller artworkInstaller)
{
    public SteamUsersLookupResult GetAvailableUsers()
    {
        var steamRoot = steamLocator.FindRoot();
        if (string.IsNullOrWhiteSpace(steamRoot))
            return SteamUsersLookupResult.Fail("Steam not found.", 2);

        var users = steamLocator.ListUsers(steamRoot);
        if (users.Count == 0)
            return SteamUsersLookupResult.Fail("No Steam users found.", 3);

        return SteamUsersLookupResult.Ok(steamRoot, users);
    }

    public SetupCompletionResult CompleteSetup(string steamRoot, SteamUserInfo selectedUser)
    {
        var existing = configStore.TryLoad();
        var cfg = existing ?? new AppConfig();

        cfg.SteamUserId = selectedUser.UserId;
        cfg.SteamPath = steamRoot;
        cfg.SchemaVersion = 1;

        configStore.Save(cfg);
        var configPath = configStore.GetConfigPath();

        var bootstrap = BootstrapStartSdShortcut(cfg);
        return new SetupCompletionResult(configPath, bootstrap);
    }

    private SdShortcutBootstrapResult BootstrapStartSdShortcut(AppConfig cfg)
    {
        if (!OperatingSystem.IsLinux())
            return SdShortcutBootstrapResult.Info("SD CARD shortcut: skipped (only supported on Linux at this stage).");

        var scriptPath = assetLocator.ResolveOutputFilePath("insertplay-start-bootstrap.sh");
        if (scriptPath is null)
            return SdShortcutBootstrapResult.Error("SD CARD shortcut: local script not found in the execution directory.");

        var iconPath = assetLocator.ResolveOutputFilePath(Path.Combine("assets", "start-sd-icon.png")) ?? string.Empty;
        var appId = AppIdCalculator.Calculate(StartSdShortcutDefaults.ExePath, StartSdShortcutDefaults.AppName);

        var entry = new SteamShortcutEntry
        {
            AppId = appId,
            AppName = StartSdShortcutDefaults.AppName,
            Exe = StartSdShortcutDefaults.ExePath,
            StartDir = assetLocator.BaseDirectory,
            LaunchOptions = Quote(scriptPath),
            Icon = iconPath,
            IsHidden = false
        };

        var ok = shortcutRepository.AddOrUpdate(cfg.SteamPath, cfg.SteamUserId, entry, out var shortcutMessage);
        runtimeEnvironmentWriter.Save(cfg, appId);

        if (!ok)
            return SdShortcutBootstrapResult.Error($"SD CARD shortcut: {shortcutMessage}");

        var artwork = new SteamArtworkPaths(
            assetLocator.ResolveOutputFilePath(Path.Combine("assets", "start-sd-capsule.png")),
            assetLocator.ResolveOutputFilePath(Path.Combine("assets", "start-sd-horizontal.png")),
            assetLocator.ResolveOutputFilePath(Path.Combine("assets", "start-sd-hero.png")),
            assetLocator.ResolveOutputFilePath(Path.Combine("assets", "start-sd-logo.png")));

        var copied = artworkInstaller.Apply(cfg.SteamPath, cfg.SteamUserId, appId, artwork);
        var artworkLine = copied > 0
            ? $"Start SD artwork updated in Steam grid with AppId {appId}."
            : "Start SD artwork: no files found to copy.";

        return SdShortcutBootstrapResult.Info($"SD CARD shortcut configured. {shortcutMessage}", artworkLine);
    }

    private static string Quote(string value) => $"\"{value}\"";
}
