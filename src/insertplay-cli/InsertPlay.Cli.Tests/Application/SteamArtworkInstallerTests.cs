using InsertPlay.Cli.Application.Services;
using InsertPlay.Cli.Tests.TestDoubles;

namespace InsertPlay.Cli.Tests.Application;

public class SteamArtworkInstallerTests
{
    [Fact]
    public void Apply_CopiesOnlyFilesThatExist_UsingSteamsNamingConvention()
    {
        var fileSystem = new FakeFileSystem();
        fileSystem.AddFile("/capsule.png", "capsule-bytes");
        fileSystem.AddFile("/horizontal.png", "horizontal-bytes");
        // hero and logo intentionally missing

        var installer = new SteamArtworkInstaller(fileSystem);
        var artwork = new SteamArtworkPaths("/capsule.png", "/horizontal.png", "/missing-hero.png", null);

        var copied = installer.Apply("/steam", "12345", 999u, artwork);

        Assert.Equal(2, copied);
        Assert.True(fileSystem.FileExists("/steam/userdata/12345/config/grid/999p.png"));
        Assert.True(fileSystem.FileExists("/steam/userdata/12345/config/grid/999.png"));
        Assert.False(fileSystem.FileExists("/steam/userdata/12345/config/grid/999_hero.png"));
        Assert.False(fileSystem.FileExists("/steam/userdata/12345/config/grid/999_logo.png"));
    }

    [Fact]
    public void Apply_ReturnsZero_WhenNoArtworkFilesExist()
    {
        var fileSystem = new FakeFileSystem();
        var installer = new SteamArtworkInstaller(fileSystem);
        var artwork = new SteamArtworkPaths(null, null, null, null);

        var copied = installer.Apply("/steam", "12345", 1u, artwork);

        Assert.Equal(0, copied);
    }
}
