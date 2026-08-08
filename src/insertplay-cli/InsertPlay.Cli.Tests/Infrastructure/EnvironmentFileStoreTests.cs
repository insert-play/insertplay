using InsertPlay.Cli.Infrastructure.Config;
using InsertPlay.Cli.Infrastructure.FileSystem;

namespace InsertPlay.Cli.Tests.Infrastructure;

public class EnvironmentFileStoreTests : IDisposable
{
    private readonly string _tempPath = Path.Combine(Path.GetTempPath(), $"insertplay-env-tests-{Guid.NewGuid():N}.env");
    private readonly EnvironmentFileStore _store = new(new PhysicalFileSystem());

    public void Dispose()
    {
        if (File.Exists(_tempPath))
            File.Delete(_tempPath);

        var tmp = _tempPath + ".tmp";
        if (File.Exists(tmp))
            File.Delete(tmp);
    }

    [Fact]
    public void WriteThenRead_RoundTripsPlainValues()
    {
        var variables = new Dictionary<string, string>
        {
            ["INSERTPLAY_SD_APP_ID"] = "123",
            ["INSERTPLAY_STEAM_PATH"] = "/home/user/.steam/steam"
        };

        _store.Write(_tempPath, variables);
        var ok = _store.TryRead(_tempPath, out var read, out var error);

        Assert.True(ok);
        Assert.Empty(error);
        Assert.Equal("123", read["INSERTPLAY_SD_APP_ID"]);
        Assert.Equal("/home/user/.steam/steam", read["INSERTPLAY_STEAM_PATH"]);
    }

    [Fact]
    public void WriteThenRead_RoundTripsValuesContainingSingleQuotes()
    {
        var variables = new Dictionary<string, string>
        {
            ["INSERTPLAY_SPECIFIC_PROCESS_NAME"] = "it's a game's process"
        };

        _store.Write(_tempPath, variables);
        var ok = _store.TryRead(_tempPath, out var read, out _);

        Assert.True(ok);
        Assert.Equal("it's a game's process", read["INSERTPLAY_SPECIFIC_PROCESS_NAME"]);
    }

    [Fact]
    public void Write_SkipsVariablesWithInvalidKeys()
    {
        var variables = new Dictionary<string, string>
        {
            ["1_INVALID_KEY"] = "value",
            ["VALID_KEY"] = "value"
        };

        _store.Write(_tempPath, variables);
        _store.TryRead(_tempPath, out var read, out _);

        Assert.False(read.ContainsKey("1_INVALID_KEY"));
        Assert.True(read.ContainsKey("VALID_KEY"));
    }

    [Fact]
    public void TryRead_ReturnsError_WhenFileDoesNotExist()
    {
        var ok = _store.TryRead(_tempPath, out _, out var error);

        Assert.False(ok);
        Assert.Contains("not found", error);
    }
}
