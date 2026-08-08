using InsertPlay.Cli.Application.Services;
using InsertPlay.Cli.Application.UseCases.Runtime;
using InsertPlay.Cli.Domain.Configuration;
using InsertPlay.Cli.Domain.Steam;
using InsertPlay.Cli.Tests.TestDoubles;

namespace InsertPlay.Cli.Tests.Application;

public class InstallSdShortcutUseCaseTests
{
    private static FakeConfigStore ValidConfigStore() => new()
    {
        Config = new AppConfig { SteamPath = "/steam", SteamUserId = "111", SchemaVersion = 1 }
    };

    private static SteamShortcutManifest ValidManifest() => new()
    {
        SchemaVersion = 1,
        Shortcut = new SteamShortcutDefinition
        {
            AppName = "My Game",
            Launch = new SteamShortcutLaunch { Exe = "{SD_ROOT}/run.sh", Args = "", StartDir = "{SD_ROOT}" },
            LaunchVerification = new SteamShortcutLaunchVerification { ProcessName = "run.sh", TimeoutSeconds = 30 }
        }
    };

    private static InstallSdShortcutUseCase CreateUseCase(
        FakeConfigStore configStore,
        FakeShortcutRepository shortcutRepository,
        FakeShortcutIdentityStore identityStore,
        FakeShortcutManifestRepository manifestRepository,
        FakeFileSystem fileSystem,
        FakeSteamProcessController? steamProcessController = null) =>
        new(configStore, shortcutRepository, identityStore, manifestRepository, fileSystem,
            new SteamArtworkInstaller(fileSystem), steamProcessController ?? new FakeSteamProcessController());

    [Fact]
    public void Execute_ReturnsExitCode10_WhenConfigurationIsInvalid()
    {
        var useCase = CreateUseCase(
            new FakeConfigStore { ValidResult = false }, new FakeShortcutRepository(),
            new FakeShortcutIdentityStore(), new FakeShortcutManifestRepository(), new FakeFileSystem());

        var result = useCase.Execute("/sd", false);

        Assert.Equal(10, result.ExitCode);
        Assert.Equal("InsertPlay runtime configuration not found. Run 'insertplay setup' first.", result.ErrorMessage);
    }

    [Fact]
    public void Execute_ReturnsUsageError_WhenSdRootIsMissing()
    {
        var useCase = CreateUseCase(
            ValidConfigStore(), new FakeShortcutRepository(), new FakeShortcutIdentityStore(),
            new FakeShortcutManifestRepository(), new FakeFileSystem());

        var result = useCase.Execute(null, false);

        Assert.Equal(1, result.ExitCode);
    }

    [Fact]
    public void Execute_ReturnsExitCode1_WhenSdRootDirectoryDoesNotExist()
    {
        var useCase = CreateUseCase(
            ValidConfigStore(), new FakeShortcutRepository(), new FakeShortcutIdentityStore(),
            new FakeShortcutManifestRepository(), new FakeFileSystem());

        var result = useCase.Execute("/sd", false);

        Assert.Equal(1, result.ExitCode);
        Assert.Contains("SD root not found", result.ErrorMessage);
    }

    [Fact]
    public void Execute_ReturnsExitCode11_WhenManifestFileIsMissing()
    {
        var fileSystem = new FakeFileSystem();
        fileSystem.AddDirectory("/sd");

        var useCase = CreateUseCase(
            ValidConfigStore(), new FakeShortcutRepository(), new FakeShortcutIdentityStore(),
            new FakeShortcutManifestRepository(), fileSystem);
        var result = useCase.Execute("/sd", false);

        Assert.Equal(11, result.ExitCode);
        Assert.Contains("Manifest not found", result.ErrorMessage);
    }

    [Fact]
    public void Execute_ReturnsExitCode12_WhenManifestIsInvalid()
    {
        var fileSystem = new FakeFileSystem();
        fileSystem.AddDirectory("/sd");
        fileSystem.AddFile("/sd/steam-shortcut.json", "{}");

        var manifestRepository = new FakeShortcutManifestRepository { Result = false, Error = "bad manifest" };
        var useCase = CreateUseCase(
            ValidConfigStore(), new FakeShortcutRepository(), new FakeShortcutIdentityStore(), manifestRepository, fileSystem);
        var result = useCase.Execute("/sd", false);

        Assert.Equal(12, result.ExitCode);
        Assert.Equal("bad manifest", result.ErrorMessage);
    }

    [Fact]
    public void Execute_ReturnsExitCode12_WhenIdentityCannotBeResolved()
    {
        var fileSystem = new FakeFileSystem();
        fileSystem.AddDirectory("/sd");
        fileSystem.AddFile("/sd/steam-shortcut.json", "{}");

        var manifestRepository = new FakeShortcutManifestRepository { Result = true, Manifest = ValidManifest() };
        var identityStore = new FakeShortcutIdentityStore { GetOrCreateResult = false, GetOrCreateError = "guid conflict" };

        var useCase = CreateUseCase(ValidConfigStore(), new FakeShortcutRepository(), identityStore, manifestRepository, fileSystem);
        var result = useCase.Execute("/sd", false);

        Assert.Equal(12, result.ExitCode);
        Assert.Equal("guid conflict", result.ErrorMessage);
    }

    [Fact]
    public void Execute_ReturnsExitCode13_WhenShortcutRepositoryRejectsTheShortcut()
    {
        var fileSystem = new FakeFileSystem();
        fileSystem.AddDirectory("/sd");
        fileSystem.AddFile("/sd/steam-shortcut.json", "{}");

        var manifestRepository = new FakeShortcutManifestRepository { Result = true, Manifest = ValidManifest() };
        var identityStore = new FakeShortcutIdentityStore { GetOrCreateResult = true, GetOrCreateGuid = Guid.NewGuid().ToString("D") };
        var shortcutRepository = new FakeShortcutRepository { AddOrUpdateResult = false, AddOrUpdateMessage = "vdf locked" };

        var useCase = CreateUseCase(ValidConfigStore(), shortcutRepository, identityStore, manifestRepository, fileSystem);
        var result = useCase.Execute("/sd", false);

        Assert.Equal(13, result.ExitCode);
        Assert.Equal("vdf locked", result.ErrorMessage);
    }

    [Fact]
    public void Execute_InstallsShortcutAndSavesRuntimeShortcutEnvironment_OnSuccess()
    {
        var fileSystem = new FakeFileSystem();
        fileSystem.AddDirectory("/sd");
        fileSystem.AddFile("/sd/steam-shortcut.json", "{}");

        var manifestRepository = new FakeShortcutManifestRepository { Result = true, Manifest = ValidManifest() };
        var guid = Guid.NewGuid().ToString("D");
        var identityStore = new FakeShortcutIdentityStore { GetOrCreateResult = true, GetOrCreateGuid = guid };
        var shortcutRepository = new FakeShortcutRepository();
        var configStore = ValidConfigStore();

        var useCase = CreateUseCase(configStore, shortcutRepository, identityStore, manifestRepository, fileSystem);
        var result = useCase.Execute("/sd", restartSteam: false);

        Assert.Equal(0, result.ExitCode);
        Assert.Null(result.ErrorMessage);
        Assert.Single(shortcutRepository.Entries);
        Assert.Equal("/sd/run.sh", shortcutRepository.Entries[0].Exe);
        Assert.Contains($"--guid {guid}", shortcutRepository.Entries[0].LaunchOptions);
        Assert.True(configStore.SavedShortcutEnvironments.ContainsKey(guid));
        Assert.Contains(result.StandardOutputLines, l => l.Contains("Specific shortcut installed"));
    }

    [Fact]
    public void Execute_InstallsShortcutWithoutProcessVerificationEnv_WhenManifestOmitsLaunchVerification()
    {
        var fileSystem = new FakeFileSystem();
        fileSystem.AddDirectory("/sd");
        fileSystem.AddFile("/sd/steam-shortcut.json", "{}");

        var manifest = ValidManifest();
        manifest.Shortcut!.LaunchVerification = null;
        var manifestRepository = new FakeShortcutManifestRepository { Result = true, Manifest = manifest };
        var guid = Guid.NewGuid().ToString("D");
        var identityStore = new FakeShortcutIdentityStore { GetOrCreateResult = true, GetOrCreateGuid = guid };
        var shortcutRepository = new FakeShortcutRepository();
        var configStore = ValidConfigStore();

        var useCase = CreateUseCase(configStore, shortcutRepository, identityStore, manifestRepository, fileSystem);
        var result = useCase.Execute("/sd", restartSteam: false);

        Assert.Equal(0, result.ExitCode);
        Assert.Null(result.ErrorMessage);
        var savedEnv = configStore.SavedShortcutEnvironments[guid];
        Assert.False(savedEnv.ContainsKey("INSERTPLAY_SPECIFIC_PROCESS_NAME"));
        Assert.False(savedEnv.ContainsKey("INSERTPLAY_SPECIFIC_TIMEOUT_SECONDS"));
    }

    [Fact]
    public void Execute_AttemptsSteamRestart_WhenRequested()
    {
        var fileSystem = new FakeFileSystem();
        fileSystem.AddDirectory("/sd");
        fileSystem.AddFile("/sd/steam-shortcut.json", "{}");

        var manifestRepository = new FakeShortcutManifestRepository { Result = true, Manifest = ValidManifest() };
        var identityStore = new FakeShortcutIdentityStore { GetOrCreateResult = true, GetOrCreateGuid = Guid.NewGuid().ToString("D") };
        var steamProcessController = new FakeSteamProcessController { RestartResult = true, RestartMessage = "restarted" };

        var useCase = CreateUseCase(
            ValidConfigStore(), new FakeShortcutRepository(), identityStore, manifestRepository, fileSystem, steamProcessController);
        var result = useCase.Execute("/sd", restartSteam: true);

        Assert.Equal(0, result.ExitCode);
        Assert.Equal("restarted", result.RestartMessage);
        Assert.False(result.RestartFailed);
    }
}
