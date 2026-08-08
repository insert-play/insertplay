using InsertPlay.Cli.Application.Services;
using InsertPlay.Cli.Application.UseCases.Setup;
using InsertPlay.Cli.Domain.Steam;
using InsertPlay.Cli.Tests.TestDoubles;

namespace InsertPlay.Cli.Tests.Application;

// These tests assume they run on Linux (true in this repo's dev/CI environment), since
// SetupUseCase.CompleteSetup only attempts the SD CARD bootstrap shortcut on Linux.
public class SetupUseCaseTests
{
    private static SetupUseCase CreateUseCase(
        FakeSteamInstallationLocator locator,
        FakeConfigStore configStore,
        FakeShortcutRepository shortcutRepository,
        FakeBundledAssetLocator assetLocator,
        FakeFileSystem fileSystem) =>
        new(locator, configStore, shortcutRepository, assetLocator, new RuntimeEnvironmentWriter(configStore), new SteamArtworkInstaller(fileSystem));

    [Fact]
    public void GetAvailableUsers_ReturnsError_WhenSteamNotFound()
    {
        var useCase = CreateUseCase(
            new FakeSteamInstallationLocator(), new FakeConfigStore(), new FakeShortcutRepository(),
            new FakeBundledAssetLocator(), new FakeFileSystem());

        var result = useCase.GetAvailableUsers();

        Assert.False(result.Success);
        Assert.Equal(2, result.ExitCode);
        Assert.Equal("Steam not found.", result.Error);
    }

    [Fact]
    public void GetAvailableUsers_ReturnsError_WhenNoUsersFound()
    {
        var locator = new FakeSteamInstallationLocator { Root = "/steam" };
        var useCase = CreateUseCase(
            locator, new FakeConfigStore(), new FakeShortcutRepository(), new FakeBundledAssetLocator(), new FakeFileSystem());

        var result = useCase.GetAvailableUsers();

        Assert.False(result.Success);
        Assert.Equal(3, result.ExitCode);
        Assert.Equal("No Steam users found.", result.Error);
    }

    [Fact]
    public void GetAvailableUsers_ReturnsUsers_WhenFound()
    {
        var locator = new FakeSteamInstallationLocator { Root = "/steam" };
        locator.Users.Add(new SteamUserInfo("111", "acct", "Persona", true));
        var useCase = CreateUseCase(
            locator, new FakeConfigStore(), new FakeShortcutRepository(), new FakeBundledAssetLocator(), new FakeFileSystem());

        var result = useCase.GetAvailableUsers();

        Assert.True(result.Success);
        Assert.Equal("/steam", result.SteamRoot);
        Assert.Single(result.Users);
    }

    [Fact]
    public void CompleteSetup_ReportsScriptNotFound_WhenBootstrapScriptIsMissing()
    {
        var configStore = new FakeConfigStore();
        var shortcutRepository = new FakeShortcutRepository();
        var assetLocator = new FakeBundledAssetLocator(); // no files registered -> script not found
        var useCase = CreateUseCase(new FakeSteamInstallationLocator(), configStore, shortcutRepository, assetLocator, new FakeFileSystem());

        var user = new SteamUserInfo("111", "acct", "Persona", true);
        var result = useCase.CompleteSetup("/steam", user);

        Assert.Equal("/config/config.json", result.ConfigPath);
        Assert.Equal(
            "SD CARD shortcut: local script not found in the execution directory.",
            result.ShortcutBootstrap.ErrorMessage);
        Assert.Empty(result.ShortcutBootstrap.InfoLines);
        Assert.Empty(shortcutRepository.Entries);
    }

    [Fact]
    public void CompleteSetup_ConfiguresShortcutAndCopiesArtwork_WhenBootstrapScriptIsFound()
    {
        var configStore = new FakeConfigStore();
        var shortcutRepository = new FakeShortcutRepository();
        var fileSystem = new FakeFileSystem();
        fileSystem.AddFile("/app/capsule.png", "bytes");

        var assetLocator = new FakeBundledAssetLocator();
        assetLocator.Files["insertplay-start-bootstrap.sh"] = "/app/insertplay-start-bootstrap.sh";
        assetLocator.Files[Path.Combine("assets", "start-sd-capsule.png")] = "/app/capsule.png";

        var useCase = CreateUseCase(new FakeSteamInstallationLocator(), configStore, shortcutRepository, assetLocator, fileSystem);

        var user = new SteamUserInfo("111", "acct", "Persona", true);
        var result = useCase.CompleteSetup("/steam", user);

        Assert.Single(shortcutRepository.Entries);
        Assert.Equal("SD CARD", shortcutRepository.Entries[0].AppName);
        Assert.Null(result.ShortcutBootstrap.ErrorMessage);
        Assert.Contains(result.ShortcutBootstrap.InfoLines, l => l.StartsWith("SD CARD shortcut configured."));
        Assert.Contains(result.ShortcutBootstrap.InfoLines, l => l.StartsWith("Start SD artwork updated"));
        Assert.NotNull(configStore.LastSavedRuntimeEnvironment);
    }

    [Fact]
    public void CompleteSetup_ReportsShortcutFailure_WhenRepositoryRejectsIt()
    {
        var configStore = new FakeConfigStore();
        var shortcutRepository = new FakeShortcutRepository { AddOrUpdateResult = false, AddOrUpdateMessage = "disk full" };
        var assetLocator = new FakeBundledAssetLocator();
        assetLocator.Files["insertplay-start-bootstrap.sh"] = "/app/insertplay-start-bootstrap.sh";

        var useCase = CreateUseCase(new FakeSteamInstallationLocator(), configStore, shortcutRepository, assetLocator, new FakeFileSystem());

        var user = new SteamUserInfo("111", "acct", "Persona", true);
        var result = useCase.CompleteSetup("/steam", user);

        Assert.Equal("SD CARD shortcut: disk full", result.ShortcutBootstrap.ErrorMessage);
        Assert.NotNull(configStore.LastSavedRuntimeEnvironment);
    }
}
