using InsertPlay.Cli.Application.UseCases.Runtime;
using InsertPlay.Cli.Domain.Configuration;
using InsertPlay.Cli.Domain.Steam;
using InsertPlay.Cli.Tests.TestDoubles;

namespace InsertPlay.Cli.Tests.Application;

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
    public void Execute_ReturnsExitCode10_WhenGenericRuntimeEnvIsMissing()
    {
        var fileSystem = new FakeFileSystem();
        fileSystem.AddDirectory("/sd");

        var useCase = CreateUseCase(
            new FakeConfigStore(), new FakeShortcutRepository(), new FakeShortcutIdentityStore(),
            new FakeEnvironmentFileStore(), fileSystem, new FakeSteamProcessController(), new FakeProcessMonitor());
        var result = useCase.Execute("/sd", null);

        Assert.Equal(10, result.ExitCode);
    }

    [Fact]
    public void Execute_LaunchesGenericShortcut_WhenNoSpecificIdentityIsAvailable()
    {
        var fileSystem = new FakeFileSystem();
        fileSystem.AddDirectory("/sd");
        var configStore = new FakeConfigStore();
        var environmentFileStore = new FakeEnvironmentFileStore();
        environmentFileStore.ReadResults[configStore.GetRuntimeEnvPath()] =
            (new Dictionary<string, string> { ["INSERTPLAY_SD_GAME_ID"] = "123" }, "");

        var identityStore = new FakeShortcutIdentityStore { ReadResult = false, ReadError = "" };
        var steamProcessController = new FakeSteamProcessController { LaunchGameResult = true };

        var useCase = CreateUseCase(
            configStore, new FakeShortcutRepository(), identityStore, environmentFileStore, fileSystem,
            steamProcessController, new FakeProcessMonitor());
        var result = useCase.Execute("/sd", null);

        Assert.Equal(0, result.ExitCode);
        Assert.Contains("123", steamProcessController.LaunchedGameIds);
        Assert.Contains(result.InfoLines, l => l.Contains("Specific runtime shortcut env not available"));
    }

    [Fact]
    public void Execute_FallsBackToGeneric_WhenSpecificShortcutIsNotInSteam()
    {
        var fileSystem = new FakeFileSystem();
        fileSystem.AddDirectory("/sd");
        var configStore = new FakeConfigStore { Config = new AppConfig { SteamPath = "/steam", SteamUserId = "111", SchemaVersion = 1 } };
        var environmentFileStore = new FakeEnvironmentFileStore();
        environmentFileStore.ReadResults[configStore.GetRuntimeEnvPath()] =
            (new Dictionary<string, string> { ["INSERTPLAY_SD_GAME_ID"] = "123" }, "");

        var guid = Guid.NewGuid().ToString("D");
        var identityStore = new FakeShortcutIdentityStore { ReadResult = true, ReadGuid = guid };
        var specificGameId = ((999UL << 32) | 0x02000000).ToString();
        environmentFileStore.ReadResults[configStore.GetRuntimeShortcutEnvPath(guid)] = (new Dictionary<string, string>
        {
            ["INSERTPLAY_SPECIFIC_GAME_ID"] = specificGameId,
            ["INSERTPLAY_SPECIFIC_PROCESS_NAME"] = "game.exe",
            ["INSERTPLAY_SPECIFIC_TIMEOUT_SECONDS"] = "10"
        }, "");

        // No matching shortcut in the repository for appId 999 -> considered missing from Steam.
        var shortcutRepository = new FakeShortcutRepository();
        var steamProcessController = new FakeSteamProcessController();

        var useCase = CreateUseCase(
            configStore, shortcutRepository, identityStore, environmentFileStore, fileSystem,
            steamProcessController, new FakeProcessMonitor());
        var result = useCase.Execute("/sd", null);

        Assert.Equal(0, result.ExitCode);
        Assert.Contains(result.InfoLines, l => l.Contains("not found in Steam shortcuts"));
        Assert.Contains("123", steamProcessController.LaunchedGameIds);
    }

    [Fact]
    public void Execute_LaunchesSpecificShortcut_WhenProcessStartsInTime()
    {
        var fileSystem = new FakeFileSystem();
        fileSystem.AddDirectory("/sd");
        var configStore = new FakeConfigStore { Config = new AppConfig { SteamPath = "/steam", SteamUserId = "111", SchemaVersion = 1 } };
        var environmentFileStore = new FakeEnvironmentFileStore();
        environmentFileStore.ReadResults[configStore.GetRuntimeEnvPath()] =
            (new Dictionary<string, string> { ["INSERTPLAY_SD_GAME_ID"] = "123" }, "");

        var guid = Guid.NewGuid().ToString("D");
        var identityStore = new FakeShortcutIdentityStore { ReadResult = true, ReadGuid = guid };
        const uint appId = 999u;
        var specificGameId = (((ulong)appId << 32) | 0x02000000).ToString();
        environmentFileStore.ReadResults[configStore.GetRuntimeShortcutEnvPath(guid)] = (new Dictionary<string, string>
        {
            ["INSERTPLAY_SPECIFIC_GAME_ID"] = specificGameId,
            ["INSERTPLAY_SPECIFIC_PROCESS_NAME"] = "game.exe",
            ["INSERTPLAY_SPECIFIC_TIMEOUT_SECONDS"] = "10"
        }, "");

        var shortcutRepository = new FakeShortcutRepository();
        shortcutRepository.Entries.Add(new SteamShortcutEntry { AppId = appId, AppName = "Specific Game" });

        var steamProcessController = new FakeSteamProcessController();
        var processMonitor = new FakeProcessMonitor { WaitResult = true };

        var useCase = CreateUseCase(
            configStore, shortcutRepository, identityStore, environmentFileStore, fileSystem,
            steamProcessController, processMonitor);
        var result = useCase.Execute("/sd", null);

        Assert.Equal(0, result.ExitCode);
        Assert.Contains(specificGameId, steamProcessController.LaunchedGameIds);
        Assert.DoesNotContain("123", steamProcessController.LaunchedGameIds);
        Assert.Contains(result.InfoLines, l => l.Contains("Specific launch succeeded."));
    }

    [Fact]
    public void Execute_ReportsInvalidExplicitGuid()
    {
        var fileSystem = new FakeFileSystem();
        fileSystem.AddDirectory("/sd");
        var configStore = new FakeConfigStore();
        var environmentFileStore = new FakeEnvironmentFileStore();
        environmentFileStore.ReadResults[configStore.GetRuntimeEnvPath()] =
            (new Dictionary<string, string> { ["INSERTPLAY_SD_GAME_ID"] = "123" }, "");

        var useCase = CreateUseCase(
            configStore, new FakeShortcutRepository(), new FakeShortcutIdentityStore(), environmentFileStore,
            fileSystem, new FakeSteamProcessController(), new FakeProcessMonitor());
        var result = useCase.Execute("/sd", "not-a-guid");

        Assert.Contains(result.InfoLines, l => l.Contains("Provided shortcut GUID is invalid"));
        Assert.Equal(0, result.ExitCode);
    }

    [Fact]
    public void Execute_ReturnsExitCode16_WhenGenericLaunchFails()
    {
        var fileSystem = new FakeFileSystem();
        fileSystem.AddDirectory("/sd");
        var configStore = new FakeConfigStore();
        var environmentFileStore = new FakeEnvironmentFileStore();
        environmentFileStore.ReadResults[configStore.GetRuntimeEnvPath()] =
            (new Dictionary<string, string> { ["INSERTPLAY_SD_GAME_ID"] = "123" }, "");

        var steamProcessController = new FakeSteamProcessController { LaunchGameResult = false, LaunchGameMessage = "no steam binary" };

        var useCase = CreateUseCase(
            configStore, new FakeShortcutRepository(), new FakeShortcutIdentityStore(), environmentFileStore,
            fileSystem, steamProcessController, new FakeProcessMonitor());
        var result = useCase.Execute("/sd", null);

        Assert.Equal(16, result.ExitCode);
        Assert.Equal("Failed to launch generic shortcut: no steam binary", result.ErrorMessage);
    }
}
