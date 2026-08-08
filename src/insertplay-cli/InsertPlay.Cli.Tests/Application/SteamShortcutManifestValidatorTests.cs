using InsertPlay.Cli.Application.Services;
using InsertPlay.Cli.Domain.Steam;

namespace InsertPlay.Cli.Tests.Application;

public class SteamShortcutManifestValidatorTests
{
    private static SteamShortcutManifest ValidManifest() => new()
    {
        SchemaVersion = 1,
        Shortcut = new SteamShortcutDefinition
        {
            AppName = "My Game",
            Launch = new SteamShortcutLaunch
            {
                Exe = "{SD_ROOT}/game/run.sh",
                Args = "--fullscreen",
                StartDir = "{SD_ROOT}/game"
            },
            LaunchVerification = new SteamShortcutLaunchVerification
            {
                ProcessName = "run.sh",
                TimeoutSeconds = 30
            }
        }
    };

    [Fact]
    public void Validate_AcceptsAWellFormedManifest()
    {
        var ok = SteamShortcutManifestValidator.Validate(ValidManifest(), out var error);

        Assert.True(ok);
        Assert.Empty(error);
    }

    [Fact]
    public void Validate_RejectsNullManifest()
    {
        var ok = SteamShortcutManifestValidator.Validate(null, out var error);

        Assert.False(ok);
        Assert.Equal("Manifest is empty.", error);
    }

    [Fact]
    public void Validate_RejectsUnsupportedSchemaVersion()
    {
        var manifest = ValidManifest();
        manifest.SchemaVersion = 2;

        var ok = SteamShortcutManifestValidator.Validate(manifest, out var error);

        Assert.False(ok);
        Assert.Contains("schemaVersion", error);
    }

    [Fact]
    public void Validate_RejectsMissingShortcut()
    {
        var manifest = new SteamShortcutManifest { SchemaVersion = 1, Shortcut = null };

        var ok = SteamShortcutManifestValidator.Validate(manifest, out var error);

        Assert.False(ok);
        Assert.Equal("Manifest field 'shortcut' is required.", error);
    }

    [Fact]
    public void Validate_RejectsBlankAppName()
    {
        var manifest = ValidManifest();
        manifest.Shortcut!.AppName = "   ";

        var ok = SteamShortcutManifestValidator.Validate(manifest, out var error);

        Assert.False(ok);
        Assert.Equal("Manifest field 'shortcut.appName' is required.", error);
    }

    [Fact]
    public void Validate_RejectsInvalidGuid()
    {
        var manifest = ValidManifest();
        manifest.Shortcut!.Guid = "not-a-guid";

        var ok = SteamShortcutManifestValidator.Validate(manifest, out var error);

        Assert.False(ok);
        Assert.Contains("shortcut.guid", error);
    }

    [Fact]
    public void Validate_AcceptsAValidGuid()
    {
        var manifest = ValidManifest();
        manifest.Shortcut!.Guid = Guid.NewGuid().ToString();

        var ok = SteamShortcutManifestValidator.Validate(manifest, out var error);

        Assert.True(ok);
        Assert.Empty(error);
    }

    [Fact]
    public void Validate_RejectsMissingLaunch()
    {
        var manifest = ValidManifest();
        manifest.Shortcut!.Launch = null;

        var ok = SteamShortcutManifestValidator.Validate(manifest, out var error);

        Assert.False(ok);
        Assert.Equal("Manifest field 'shortcut.launch' is required.", error);
    }

    [Theory]
    [InlineData("")]
    [InlineData("   ")]
    public void Validate_RejectsBlankLaunchExe(string exe)
    {
        var manifest = ValidManifest();
        manifest.Shortcut!.Launch!.Exe = exe;

        var ok = SteamShortcutManifestValidator.Validate(manifest, out var error);

        Assert.False(ok);
        Assert.Equal("Manifest field 'shortcut.launch.exe' is required.", error);
    }

    [Fact]
    public void Validate_RejectsBlankLaunchArgs()
    {
        var manifest = ValidManifest();
        manifest.Shortcut!.Launch!.Args = "   ";

        var ok = SteamShortcutManifestValidator.Validate(manifest, out var error);

        Assert.False(ok);
        Assert.Equal("Manifest field 'shortcut.launch.args' is required.", error);
    }

    [Fact]
    public void Validate_RejectsBlankLaunchStartDir()
    {
        var manifest = ValidManifest();
        manifest.Shortcut!.Launch!.StartDir = "   ";

        var ok = SteamShortcutManifestValidator.Validate(manifest, out var error);

        Assert.False(ok);
        Assert.Equal("Manifest field 'shortcut.launch.startDir' is required.", error);
    }

    [Fact]
    public void Validate_AcceptsMissingLaunchVerification()
    {
        var manifest = ValidManifest();
        manifest.Shortcut!.LaunchVerification = null;

        var ok = SteamShortcutManifestValidator.Validate(manifest, out var error);

        Assert.True(ok);
        Assert.Empty(error);
    }

    [Fact]
    public void Validate_RejectsBlankProcessName()
    {
        var manifest = ValidManifest();
        manifest.Shortcut!.LaunchVerification!.ProcessName = "   ";

        var ok = SteamShortcutManifestValidator.Validate(manifest, out var error);

        Assert.False(ok);
        Assert.Equal("Manifest field 'shortcut.launchVerification.processName' is required.", error);
    }

    [Theory]
    [InlineData(0)]
    [InlineData(-1)]
    public void Validate_RejectsNonPositiveTimeout(int timeoutSeconds)
    {
        var manifest = ValidManifest();
        manifest.Shortcut!.LaunchVerification!.TimeoutSeconds = timeoutSeconds;

        var ok = SteamShortcutManifestValidator.Validate(manifest, out var error);

        Assert.False(ok);
        Assert.Equal("Manifest field 'shortcut.launchVerification.timeoutSeconds' must be a positive integer.", error);
    }
}
