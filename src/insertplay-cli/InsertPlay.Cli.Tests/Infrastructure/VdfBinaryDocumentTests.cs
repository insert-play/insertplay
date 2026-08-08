using InsertPlay.Cli.Domain.Steam;
using InsertPlay.Cli.Infrastructure.Steam.Vdf;

namespace InsertPlay.Cli.Tests.Infrastructure;

public class VdfBinaryDocumentTests : IDisposable
{
    private readonly string _tempPath = Path.Combine(Path.GetTempPath(), $"insertplay-vdf-tests-{Guid.NewGuid():N}.vdf");

    public void Dispose()
    {
        if (File.Exists(_tempPath))
            File.Delete(_tempPath);
    }

    [Fact]
    public void SaveThenLoad_RoundTripsAllFields()
    {
        var doc = new VdfBinaryDocument();
        var entry = new SteamShortcutEntry
        {
            AppId = 123456789,
            AppName = "My Game",
            Exe = "/bin/bash",
            StartDir = "/home/user/game",
            Icon = "/home/user/game/icon.png",
            ShortcutPath = "",
            LaunchOptions = "--fullscreen"
        };
        entry.Tags.Add("InsertPlay");
        entry.Tags.Add("SD Card");
        entry.IsHidden = true;
        doc.Shortcuts.Add(entry);

        doc.Save(_tempPath);
        var loaded = VdfBinaryDocument.Load(_tempPath);

        Assert.Single(loaded.Shortcuts);
        var loadedEntry = loaded.Shortcuts[0];
        Assert.Equal(123456789u, loadedEntry.AppId);
        Assert.Equal("My Game", loadedEntry.AppName);
        Assert.Equal("/bin/bash", loadedEntry.Exe);
        Assert.Equal("/home/user/game", loadedEntry.StartDir);
        Assert.Equal("/home/user/game/icon.png", loadedEntry.Icon);
        Assert.Equal("--fullscreen", loadedEntry.LaunchOptions);
        Assert.True(loadedEntry.IsHidden);
        Assert.Equal(["InsertPlay", "SD Card"], loadedEntry.Tags);
    }

    [Fact]
    public void SaveThenLoad_RoundTripsMultipleShortcutsInOrder()
    {
        var doc = new VdfBinaryDocument();
        doc.Shortcuts.Add(new SteamShortcutEntry { AppName = "First", Exe = "/bin/one" });
        doc.Shortcuts.Add(new SteamShortcutEntry { AppName = "Second", Exe = "/bin/two" });

        doc.Save(_tempPath);
        var loaded = VdfBinaryDocument.Load(_tempPath);

        Assert.Equal(["First", "Second"], loaded.Shortcuts.Select(s => s.AppName));
    }

    [Fact]
    public void Load_ReturnsEmptyDocument_WhenFileDoesNotExist()
    {
        var loaded = VdfBinaryDocument.Load(_tempPath);

        Assert.Empty(loaded.Shortcuts);
    }
}
