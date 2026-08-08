using InsertPlay.Cli.Domain.Steam;

namespace InsertPlay.Cli.Tests.Domain;

public class AppIdCalculatorTests
{
    [Fact]
    public void Calculate_IsDeterministic_ForTheSameInput()
    {
        var first = AppIdCalculator.Calculate("/bin/bash", "SD CARD");
        var second = AppIdCalculator.Calculate("/bin/bash", "SD CARD");

        Assert.Equal(first, second);
    }

    [Fact]
    public void Calculate_AlwaysSetsTheHighBit()
    {
        var appId = AppIdCalculator.Calculate("/usr/bin/steam", "Some Game");

        Assert.Equal(0x80000000u, appId & 0x80000000u);
    }

    [Fact]
    public void Calculate_DiffersForDifferentExeOrAppName()
    {
        var a = AppIdCalculator.Calculate("/bin/bash", "SD CARD");
        var b = AppIdCalculator.Calculate("/bin/bash", "Other Game");
        var c = AppIdCalculator.Calculate("/usr/bin/other", "SD CARD");

        Assert.NotEqual(a, b);
        Assert.NotEqual(a, c);
    }

    [Fact]
    public void Calculate_MatchesKnownValue_ForStartSdShortcut()
    {
        // Locks in the exact value the "SD CARD" bootstrap shortcut has always produced,
        // so a change to the CRC32/bit-composition logic doesn't silently reshuffle Steam's grid artwork.
        var appId = AppIdCalculator.Calculate("/bin/bash", "SD CARD");

        Assert.Equal(2577431528u, appId);
    }
}
