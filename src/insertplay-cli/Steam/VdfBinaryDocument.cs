using System.Text;

namespace InsertPlay.Cli.Steam;

public sealed class VdfBinaryDocument
{
    public List<VdfShortcutEntry> Shortcuts { get; } = [];

    public static VdfBinaryDocument Load(string path)
    {
        var doc = new VdfBinaryDocument();
        if (!File.Exists(path))
            return doc;

        using var stream = File.OpenRead(path);
        using var reader = new BinaryReader(stream, Encoding.UTF8, false);

        try
        {
            if (reader.ReadByte() != 0x00)
                return doc;

            var root = ReadString(reader);
            if (!root.Equals("shortcuts", StringComparison.OrdinalIgnoreCase))
                return doc;

            while (reader.BaseStream.Position < reader.BaseStream.Length)
            {
                var type = reader.ReadByte();
                if (type == 0x08)
                    break;
                if (type != 0x00)
                    break;

                _ = ReadString(reader);
                doc.Shortcuts.Add(ReadShortcut(reader));
            }
        }
        catch
        {
            return doc;
        }

        return doc;
    }

    public void Save(string path)
    {
        Directory.CreateDirectory(Path.GetDirectoryName(path)!);
        using var stream = File.Open(path, FileMode.Create, FileAccess.Write, FileShare.None);
        using var writer = new BinaryWriter(stream, Encoding.UTF8, false);

        WriteByte(writer, 0x00);
        WriteString(writer, "shortcuts");

        for (var i = 0; i < Shortcuts.Count; i++)
        {
            WriteByte(writer, 0x00);
            WriteString(writer, i.ToString());
            WriteShortcut(writer, Shortcuts[i]);
        }

        WriteByte(writer, 0x08);
        WriteByte(writer, 0x08);
    }

    private static VdfShortcutEntry ReadShortcut(BinaryReader reader)
    {
        var entry = new VdfShortcutEntry();
        while (true)
        {
            var type = reader.ReadByte();
            if (type == 0x08)
                break;

            var key = ReadString(reader);
            switch (type)
            {
                case 0x01:
                {
                    var sv = ReadString(reader);
                    switch (key.ToLowerInvariant())
                    {
                        case "appname": entry.AppName = sv; break;
                        case "exe": entry.Exe = sv; break;
                        case "startdir": entry.StartDir = sv; break;
                        case "icon": entry.Icon = sv; break;
                        case "shortcutpath": entry.ShortcutPath = sv; break;
                        case "launchoptions": entry.LaunchOptions = sv; break;
                    }

                    break;
                }
                case 0x02:
                {
                    var iv = reader.ReadInt32();
                    if (key.Equals("appid", StringComparison.OrdinalIgnoreCase))
                        entry.AppId = (uint)iv;
                    else if (key.Equals("ishidden", StringComparison.OrdinalIgnoreCase))
                        entry.IsHidden = iv != 0;
                    break;
                }
                case 0x00:
                    ReadNestedTags(reader, entry, key);
                    break;
                default:
                    return entry;
            }
        }

        return entry;
    }

    private static void ReadNestedTags(BinaryReader reader, VdfShortcutEntry entry, string parentKey)
    {
        while (true)
        {
            var type = reader.ReadByte();
            if (type == 0x08)
                break;

            var key = ReadString(reader);
            if (type == 0x01)
            {
                var value = ReadString(reader);
                if (parentKey.Equals("tags", StringComparison.OrdinalIgnoreCase))
                    entry.Tags.Add(value);
            }
            else if (type == 0x02)
            {
                _ = reader.ReadInt32();
            }
            else if (type == 0x00)
            {
                ReadNestedTags(reader, entry, key);
            }
        }
    }

    private static void WriteShortcut(BinaryWriter writer, VdfShortcutEntry e)
    {
        WriteIntField(writer, "appid", (int)e.AppId);
        WriteStringField(writer, "AppName", e.AppName);
        WriteStringField(writer, "exe", e.Exe);
        WriteStringField(writer, "StartDir", e.StartDir);
        WriteStringField(writer, "icon", e.Icon);
        WriteStringField(writer, "ShortcutPath", e.ShortcutPath);
        WriteStringField(writer, "LaunchOptions", e.LaunchOptions);
        WriteIntField(writer, "IsHidden", e.IsHidden ? 1 : 0);
        WriteIntField(writer, "AllowDesktopConfig", 1);
        WriteIntField(writer, "AllowOverlay", 1);
        WriteIntField(writer, "OpenVR", 0);
        WriteIntField(writer, "Devkit", 0);
        WriteStringField(writer, "DevkitGameID", "");
        WriteIntField(writer, "DevkitOverrideAppID", 0);
        WriteIntField(writer, "LastPlayTime", 0);
        WriteStringField(writer, "FlatpakAppID", "");

        WriteByte(writer, 0x00);
        WriteString(writer, "tags");
        for (var i = 0; i < e.Tags.Count; i++)
        {
            WriteByte(writer, 0x01);
            WriteString(writer, i.ToString());
            WriteString(writer, e.Tags[i]);
        }
        WriteByte(writer, 0x08);

        WriteByte(writer, 0x08);
    }

    private static void WriteStringField(BinaryWriter w, string key, string value)
    {
        WriteByte(w, 0x01);
        WriteString(w, key);
        WriteString(w, value);
    }

    private static void WriteIntField(BinaryWriter w, string key, int value)
    {
        WriteByte(w, 0x02);
        WriteString(w, key);
        w.Write(value);
    }

    private static string ReadString(BinaryReader reader)
    {
        var sb = new StringBuilder();
        byte b;
        while ((b = reader.ReadByte()) != 0)
            sb.Append((char)b);
        return sb.ToString();
    }

    private static void WriteString(BinaryWriter writer, string s)
    {
        writer.Write(Encoding.UTF8.GetBytes(s));
        writer.Write((byte)0x00);
    }

    private static void WriteByte(BinaryWriter writer, byte b) => writer.Write(b);
}

public sealed class VdfShortcutEntry
{
    public uint AppId { get; set; }
    public string AppName { get; set; } = "";
    public string Exe { get; set; } = "";
    public string StartDir { get; set; } = "";
    public string Icon { get; set; } = "";
    public string ShortcutPath { get; set; } = "";
    public string LaunchOptions { get; set; } = "";
    public bool IsHidden { get; set; }
    public List<string> Tags { get; set; } = [];
}
