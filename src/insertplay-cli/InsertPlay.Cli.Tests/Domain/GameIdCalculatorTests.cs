using InsertPlay.Cli.Domain.Steam;

namespace InsertPlay.Cli.Tests.Domain;

public class GameIdCalculatorTests
{
    [Theory]
    [InlineData(0u)]
    [InlineData(1u)]
    [InlineData(0x80000000u)]
    [InlineData(uint.MaxValue)]
    public void Calculate_ShiftsAppIdIntoHighBitsAndTagsWithShortcutMarker(uint appId)
    {
        var gameId = GameIdCalculator.Calculate(appId);

        Assert.Equal(((ulong)appId << 32) | 0x02000000, gameId);
        Assert.Equal(appId, (uint)(gameId >> 32));
    }
}
