using InsertPlay.Cli.Application.UseCases.Runtime;
using InsertPlay.Cli.Domain.Configuration;
using InsertPlay.Cli.Domain.Steam;
using InsertPlay.Cli.Tests.TestDoubles;

namespace InsertPlay.Cli.Tests.Application;

// autorun-launch never falls back to the generic "SD CARD" shortcut anymore: it always
// attempts the specific game_id for the shortcut identified by --guid / shortcut-identity.json,
// and fails outright (no fallback) when that isn't possible.
public class AutorunLaunchUseCaseTests
{
    private static AutorunLaunchUseCase CreateUseCase(
        FakeConfigStore configStore,
        FakeShortcutRepository shortcutRepository,
        FakeShortcutIdentityStore identityStore,
        FakeEnvironmentFileStore environmentFileStore,
        FakeFileSystem fileSystem,
        FakeSteamProcessController steamProcessController,
        FakeProcessMonitor processMonitor) =>
        new(configStore, shortcutRepository, identityStore, environmentFileStore, fileSystem, steamProcessController, processMonitor);

    private static (string Guid, string GameId) SetUpSpecificRuntimeLaunch(
        FakeConfigStore configStore, FakeEnvironmentFileStore environmentFileStore, uint appId = 999u)
    {
        var guid = Guid.NewGuid().ToString("D");
        var gameId = (((ulong)appId << 32) | 0x02000000).ToString();
        environmentFileStore.ReadResults[configStore.GetRuntimeShortcutEnvPath(guid)] = (new Dictionary<string, string>
        {
            ["INSERTPLAY_SPECIFIC_GAME_ID"] = gameId,
            ["INSERTPLAY_SPECIFIC_PROCESS_NAME"] = "game.exe",
            ["INSERTPLAY_SPECIFIC_TIMEOUT_SECONDS"] = "10"
        }, "");

        return (guid, gameId);
    }

    [Fact]
    public void Execute_ReturnsUsageError_WhenSdRootIsMissing()
    {
        var useCase = CreateUseCase(
            new FakeConfigStore(), new FakeShortcutRepository(), new FakeShortcutIdentityStore(),
            new FakeEnvironmentFileStore(), new FakeFileSystem(), new FakeSteamProcessController(), new FakeProcessMonitor());

        var result = useCase.Execute(null, null);

        Assert.Equal(1, result.ExitCode);
    }

    [Fact]
    public void Execute_ReturnsError_WhenSdRootDoesNotExist()
    {
        var useCase = CreateUseCase(
            new FakeConfigStore(), new FakeShortcutRepository(), new FakeShortcutIdentityStore(),
            new FakeEnvironmentFileStore(), new FakeFileSystem(), new FakeSteamProcessController(), new FakeProcessMonitor());

        var result = useCase.Execute("/sd", null);

        Assert.Equal(1, result.ExitCode);
    }

    [Fact]
    public void Execute_ReturnsExitCode10_WhenNoSpecificIdentityIsAvailable()
    {
        var fileSystem = new FakeFileSystem();
        fileSystem.AddDirectory("/sd");
        var identityStore = new FakeShortcutIdentityStore { ReadResult = false, ReadError = "" };
        var steamProcessController = new FakeSteamProcessController();

        var useCase = CreateUseCase(
            new FakeConfigStore(), new FakeShortcutRepository(), identityStore, new FakeEnvironmentFileStore(),
            fileSystem, steamProcessController, new FakeProcessMonitor());
        var result = useCase.Execute("/sd", null);

        Assert.Equal(10, result.ExitCode);
        Assert.Contains("No specific shortcut identity found", result.ErrorMessage);
        Assert.Empty(steamProcessController.LaunchedGameIds);
    }

    [Fact]
    public void Execute_ReturnsExitCode10_WhenSpecificRuntimeEnvIsMissing()
    {
        var fileSystem = new FakeFileSystem();
        fileSystem.AddDirectory("/sd");
        var guid = Guid.NewGuid().ToString("D");
        var identityStore = new FakeShortcutIdentityStore { ReadResult = true, ReadGuid = guid };
        var steamProcessController = new FakeSteamProcessController();

        // No entry registered in environmentFileStore.ReadResults for this guid's env path.
        var useCase = CreateUseCase(
            new FakeConfigStore(), new FakeShortcutRepository(), identityStore, new FakeEnvironmentFileStore(),
            fileSystem, steamProcessController, new FakeProcessMonitor());
        var result = useCase.Execute("/sd", null);

        Assert.Equal(10, result.ExitCode);
        Assert.Contains("not found", result.ErrorMessage);
        Assert.Empty(steamProcessController.LaunchedGameIds);
    }

    [Fact]
    public void Execute_ReturnsExitCode11_WhenSpecificShortcutIsNotInSteam()
    {
        var fileSystem = new FakeFileSystem();
        fileSystem.AddDirectory("/sd");
        var configStore = new FakeConfigStore { Config = new AppConfig { SteamPath = "/steam", SteamUserId = "111", SchemaVersion = 1 } };
        var environmentFileStore = new FakeEnvironmentFileStore();
        var (guid, _) = SetUpSpecificRuntimeLaunch(configStore, environmentFileStore);
        var identityStore = new FakeShortcutIdentityStore { ReadResult = true, ReadGuid = guid };

        // No matching shortcut in the repository for this appId -> considered missing from Steam.
        var shortcutRepository = new FakeShortcutRepository();
        var steamProcessController = new FakeSteamProcessController();

        var useCase = CreateUseCase(
            configStore, shortcutRepository, identityStore, environmentFileStore, fileSystem,
            steamProcessController, new FakeProcessMonitor());
        var result = useCase.Execute("/sd", null);

        Assert.Equal(11, result.ExitCode);
        Assert.Contains("not found in Steam shortcuts", result.ErrorMessage);
        Assert.Empty(steamProcessController.LaunchedGameIds);
    }

    [Fact]
    public void Execute_LaunchesSpecificShortcut_WhenProcessStartsInTime()
    {
        var fileSystem = new FakeFileSystem();
        fileSystem.AddDirectory("/sd");
        var configStore = new FakeConfigStore { Config = new AppConfig { SteamPath = "/steam", SteamUserId = "111", SchemaVersion = 1 } };
        var environmentFileStore = new FakeEnvironmentFileStore();
        const uint appId = 999u;
        var (guid, gameId) = SetUpSpecificRuntimeLaunch(configStore, environmentFileStore, appId);
        var identityStore = new FakeShortcutIdentityStore { ReadResult = true, ReadGuid = guid };

        var shortcutRepository = new FakeShortcutRepository();
        shortcutRepository.Entries.Add(new SteamShortcutEntry { AppId = appId, AppName = "Specific Game" });

        var steamProcessController = new FakeSteamProcessController();
        var processMonitor = new FakeProcessMonitor { WaitResult = true };

        var useCase = CreateUseCase(
            configStore, shortcutRepository, identityStore, environmentFileStore, fileSystem,
            steamProcessController, processMonitor);
        var result = useCase.Execute("/sd", null);

        Assert.Equal(0, result.ExitCode);
        Assert.Contains(gameId, steamProcessController.LaunchedGameIds);
        Assert.Contains(result.InfoLines, l => l.Contains("Specific launch succeeded."));
    }

    [Fact]
    public void Execute_SkipsSteamRegistrationCheck_WhenConfigIsInvalid()
    {
        var fileSystem = new FakeFileSystem();
        fileSystem.AddDirectory("/sd");
        var configStore = new FakeConfigStore { ValidResult = false };
        var environmentFileStore = new FakeEnvironmentFileStore();
        var (guid, gameId) = SetUpSpecificRuntimeLaunch(configStore, environmentFileStore);
        var identityStore = new FakeShortcutIdentityStore { ReadResult = true, ReadGuid = guid };

        // No shortcut repository entries at all -- would fail the "exists in Steam" check if it ran.
        var steamProcessController = new FakeSteamProcessController();
        var processMonitor = new FakeProcessMonitor { WaitResult = true };

        var useCase = CreateUseCase(
            configStore, new FakeShortcutRepository(), identityStore, environmentFileStore, fileSystem,
            steamProcessController, processMonitor);
        var result = useCase.Execute("/sd", null);

        Assert.Equal(0, result.ExitCode);
        Assert.Contains(gameId, steamProcessController.LaunchedGameIds);
    }

    [Fact]
    public void Execute_ReturnsExitCode16_WhenSpecificLaunchRequestFails()
    {
        var fileSystem = new FakeFileSystem();
        fileSystem.AddDirectory("/sd");
        var configStore = new FakeConfigStore { ValidResult = false };
        var environmentFileStore = new FakeEnvironmentFileStore();
        var (guid, _) = SetUpSpecificRuntimeLaunch(configStore, environmentFileStore);
        var identityStore = new FakeShortcutIdentityStore { ReadResult = true, ReadGuid = guid };
        var steamProcessController = new FakeSteamProcessController { LaunchGameResult = false, LaunchGameMessage = "no steam binary" };

        var useCase = CreateUseCase(
            configStore, new FakeShortcutRepository(), identityStore, environmentFileStore, fileSystem,
            steamProcessController, new FakeProcessMonitor());
        var result = useCase.Execute("/sd", null);

        Assert.Equal(16, result.ExitCode);
        Assert.Equal("Failed to launch specific shortcut: no steam binary", result.ErrorMessage);
    }

    [Fact]
    public void Execute_ReturnsExitCode16_WhenSpecificProcessIsNotDetectedInTime()
    {
        var fileSystem = new FakeFileSystem();
        fileSystem.AddDirectory("/sd");
        var configStore = new FakeConfigStore { ValidResult = false };
        var environmentFileStore = new FakeEnvironmentFileStore();
        var (guid, gameId) = SetUpSpecificRuntimeLaunch(configStore, environmentFileStore);
        var identityStore = new FakeShortcutIdentityStore { ReadResult = true, ReadGuid = guid };
        var steamProcessController = new FakeSteamProcessController();
        var processMonitor = new FakeProcessMonitor { WaitResult = false };

        var useCase = CreateUseCase(
            configStore, new FakeShortcutRepository(), identityStore, environmentFileStore, fileSystem,
            steamProcessController, processMonitor);
        var result = useCase.Execute("/sd", null);

        Assert.Equal(16, result.ExitCode);
        Assert.Contains("was not detected within", result.ErrorMessage);
        Assert.Contains(gameId, steamProcessController.LaunchedGameIds);
    }

    [Fact]
    public void Execute_ReturnsExitCode10_ForInvalidExplicitGuid()
    {
        var fileSystem = new FakeFileSystem();
        fileSystem.AddDirectory("/sd");
        var steamProcessController = new FakeSteamProcessController();

        var useCase = CreateUseCase(
            new FakeConfigStore(), new FakeShortcutRepository(), new FakeShortcutIdentityStore(), new FakeEnvironmentFileStore(),
            fileSystem, steamProcessController, new FakeProcessMonitor());
        var result = useCase.Execute("/sd", "not-a-guid");

        Assert.Equal(10, result.ExitCode);
        Assert.Contains(result.InfoLines, l => l.Contains("Provided shortcut GUID is invalid"));
        Assert.Empty(steamProcessController.LaunchedGameIds);
    }
}
