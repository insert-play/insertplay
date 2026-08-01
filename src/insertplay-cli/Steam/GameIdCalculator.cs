namespace InsertPlay.Cli.Steam;

public static class GameIdCalculator
{
    public static ulong Calculate(uint appId)
    {
        return ((ulong)appId << 32) | 0x02000000;
    }
}
