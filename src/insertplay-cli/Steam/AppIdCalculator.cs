using System.Text;

namespace InsertPlay.Cli.Steam;

public static class AppIdCalculator
{
    public static uint Calculate(string exe, string appName)
    {
        var key = Encoding.UTF8.GetBytes(exe + appName);
        var hash = ComputeCrc32(key);
        return hash | 0x80000000;
    }

    private static uint ComputeCrc32(byte[] bytes)
    {
        const uint polynomial = 0xEDB88320;
        var table = new uint[256];

        for (uint i = 0; i < 256; i++)
        {
            var temp = i;
            for (var j = 0; j < 8; j++)
                temp = (temp & 1) == 1 ? (polynomial ^ (temp >> 1)) : (temp >> 1);
            table[i] = temp;
        }

        var crc = 0xFFFFFFFFu;
        foreach (var b in bytes)
        {
            var index = (byte)((crc & 0xFF) ^ b);
            crc = (crc >> 8) ^ table[index];
        }

        return ~crc;
    }
}
