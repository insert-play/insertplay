using InsertPlay.Cli.Application.Abstractions;
using InsertPlay.Cli.Application.Services;
using InsertPlay.Cli.Domain.Steam;

namespace InsertPlay.Cli.Application.UseCases.Runtime;

public sealed record InstallSdShortcutResult(
    int ExitCode,
    string? ErrorMessage,
    IReadOnlyList<string> StandardOutputLines,
    string? RestartMessage,
    bool RestartFailed)
{
    public static InstallSdShortcutResult Error(string message, int exitCode) =>
        new(exitCode, message, [], null, false);
}

public sealed class InstallSdShortcutUseCase(
    IConfigStore configStore,
    IShortcutRepository shortcutRepository,
    IShortcutIdentityStore identityStore,
    IShortcutManifestRepository manifestRepository,
    IFileSystem fileSystem,
    SteamArtworkInstaller artworkInstaller,
    ISteamProcessController steamProcessController)
{
    public InstallSdShortcutResult Execute(string? sdRootRaw, bool restartSteam)
    {
        try
        {
            var cfg = configStore.TryLoad();
            if (!configStore.IsValid(cfg, out _))
                return InstallSdShortcutResult.Error(
                    "InsertPlay runtime configuration not found. Run 'insertplay setup' first.", 10);

            if (string.IsNullOrWhiteSpace(sdRootRaw))
                return InstallSdShortcutResult.Error(
                    "Usage: insertplay runtime install-shortcut --sd-root <path> [--restart-steam]", 1);

            var sdRoot = fileSystem.GetFullPath(sdRootRaw);
            if (!fileSystem.DirectoryExists(sdRoot))
                return InstallSdShortcutResult.Error($"SD root not found: {sdRoot}", 1);

            var manifestPath = ManifestPathResolver.Resolve(fileSystem, sdRoot);
            if (string.IsNullOrWhiteSpace(manifestPath))
                return InstallSdShortcutResult.Error(
                    $"Manifest not found. Expected one of: {Path.Combine(sdRoot, "steam-shortcut.json")} or {Path.Combine(sdRoot, "insertplay", "steam-shortcut.json")}",
                    11);

            if (!manifestRepository.TryLoad(manifestPath, out var manifest, out var manifestError))
                return InstallSdShortcutResult.Error(manifestError, 12);

            var shortcut = manifest!.Shortcut!;
            var launch = shortcut.Launch!;
            var verification = shortcut.LaunchVerification;

            var appName = shortcut.AppName.Trim();
            var exe = ResolveSdPlaceholder(launch.Exe, sdRoot);
            var argsResolved = ResolveSdPlaceholder(launch.Args, sdRoot);
            var startDir = ResolveSdPlaceholder(launch.StartDir, sdRoot);

            if (!identityStore.TryGetOrCreate(sdRoot, appName, shortcut.Guid, out var shortcutGuid, out var identityError))
                return InstallSdShortcutResult.Error(identityError, 12);

            var sdIconPath = ResolveArtworkPath(
                sdRoot, shortcut.Artwork?.Icon, "steam-artwork/icon.png", "insertplay/steam-artwork/icon.png");

            var appId = AppIdCalculator.Calculate(exe, appName);

            // Copy the icon into Steam's grid folder rather than pointing at the SD card file
            // directly, so the icon keeps working after the SD card is removed.
            var iconPath = artworkInstaller.ApplyIcon(cfg!.SteamPath, cfg.SteamUserId, appId, sdIconPath) ?? string.Empty;

            var entry = new SteamShortcutEntry
            {
                AppName = appName,
                Exe = exe,
                StartDir = startDir,
                LaunchOptions = AppendGuidArgument(argsResolved, shortcutGuid),
                Icon = iconPath,
                IsHidden = true
            };

            var ok = shortcutRepository.AddOrUpdate(cfg.SteamPath, cfg.SteamUserId, entry, out var message);
            if (!ok)
                return InstallSdShortcutResult.Error(message, 13);

            var gameId = GameIdCalculator.Calculate(appId);

            var artwork = new SteamArtworkPaths(
                ResolveArtworkPath(sdRoot, shortcut.Artwork?.Capsule, "steam-artwork/capsule.png", "insertplay/steam-artwork/capsule.png"),
                ResolveArtworkPath(sdRoot, shortcut.Artwork?.Horizontal, "steam-artwork/horizontal.png", "insertplay/steam-artwork/horizontal.png"),
                ResolveArtworkPath(sdRoot, shortcut.Artwork?.Hero, "steam-artwork/hero.png", "insertplay/steam-artwork/hero.png"),
                ResolveArtworkPath(sdRoot, shortcut.Artwork?.Logo, "steam-artwork/logo.png", "insertplay/steam-artwork/logo.png"));
            artworkInstaller.Apply(cfg.SteamPath, cfg.SteamUserId, appId, artwork);

            var runtimeShortcutEnv = new Dictionary<string, string>
            {
                ["INSERTPLAY_SHORTCUT_GUID"] = shortcutGuid,
                ["INSERTPLAY_SPECIFIC_GAME_ID"] = gameId.ToString()
            };
            if (verification is not null)
            {
                runtimeShortcutEnv["INSERTPLAY_SPECIFIC_PROCESS_NAME"] = verification.ProcessName.Trim();
                runtimeShortcutEnv["INSERTPLAY_SPECIFIC_TIMEOUT_SECONDS"] = verification.TimeoutSeconds.ToString();
            }

            var runtimeShortcutEnvPath = configStore.SaveRuntimeShortcutEnvironment(shortcutGuid, runtimeShortcutEnv);

            var outputLines = new List<string>
            {
                $"Specific shortcut installed with GUID {shortcutGuid}, AppId {appId} and GameId {gameId}.",
                $"Runtime shortcut environment saved to {runtimeShortcutEnvPath}"
            };

            string? restartMessage = null;
            var restartFailed = false;
            if (restartSteam)
            {
                if (steamProcessController.Restart(cfg.SteamPath, out var restartResultMessage))
                {
                    restartMessage = restartResultMessage;
                }
                else
                {
                    restartMessage = $"Steam restart failed: {restartResultMessage}";
                    restartFailed = true;
                }
            }

            return new InstallSdShortcutResult(0, null, outputLines, restartMessage, restartFailed);
        }
        catch (Exception ex)
        {
            return InstallSdShortcutResult.Error($"Unexpected runtime failure: {ex.Message}", 15);
        }
    }

    private static string ResolveSdPlaceholder(string value, string sdRoot) =>
        value.Replace("{SD_ROOT}", sdRoot, StringComparison.Ordinal);

    /// <summary>
    /// Lets insertplay-start.sh on the SD card refuse to launch when it isn't the card this
    /// shortcut was created for (e.g. a different SD card is currently inserted).
    /// </summary>
    private static string AppendGuidArgument(string args, string shortcutGuid) =>
        string.IsNullOrWhiteSpace(args) ? $"--guid {shortcutGuid}" : $"{args} --guid {shortcutGuid}";

    private string ResolveArtworkPath(string sdRoot, string? value, params string[] fallbackRelativePaths)
    {
        if (!string.IsNullOrWhiteSpace(value))
        {
            var replaced = ResolveSdPlaceholder(value, sdRoot).Trim();
            var absolute = Path.IsPathRooted(replaced)
                ? replaced
                : fileSystem.GetFullPath(Path.Combine(sdRoot, replaced));

            if (fileSystem.FileExists(absolute))
                return absolute;
        }

        foreach (var fallbackRelativePath in fallbackRelativePaths)
        {
            if (string.IsNullOrWhiteSpace(fallbackRelativePath))
                continue;

            var absolute = fileSystem.GetFullPath(Path.Combine(sdRoot, fallbackRelativePath));
            if (fileSystem.FileExists(absolute))
                return absolute;
        }

        return string.Empty;
    }
}
