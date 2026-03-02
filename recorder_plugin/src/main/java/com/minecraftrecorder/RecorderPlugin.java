package com.minecraftrecorder;

import org.bukkit.command.Command;
import org.bukkit.command.CommandSender;
import org.bukkit.plugin.java.JavaPlugin;

import java.io.IOException;
import java.nio.file.Path;

/**
 * Entry point for RecorderPlugin.
 *
 * <p>RCON commands (usable from Python via rcon_client.send()):
 * <pre>
 *   /recorder start [chamber] [seed]   — begin episode recording
 *   /recorder stop                     — flush &amp; close current episode
 *   /recorder status                   — print current file path to console
 * </pre>
 *
 * <p>Episodes are written to &lt;repo-root&gt;/episodes/ matching the Python format:
 * <pre>{chamber}_{seed}_{yyyyMMdd'T'HHmmss'Z'}.jsonl</pre>
 */
public class RecorderPlugin extends JavaPlugin {

    private EpisodeWriter          writer;
    private PlayerRecorderListener listener;

    @Override
    public void onEnable() {
        listener = new PlayerRecorderListener(this);
        getServer().getPluginManager().registerEvents(listener, this);
        getLogger().info("RecorderPlugin enabled. Use /recorder start <chamber> [seed] to begin.");
    }

    @Override
    public void onDisable() {
        stopRecording();
        getLogger().info("RecorderPlugin disabled.");
    }

    @Override
    public boolean onCommand(CommandSender sender, Command cmd, String label, String[] args) {
        if (!cmd.getName().equalsIgnoreCase("recorder")) return false;
        if (args.length == 0) {
            sender.sendMessage("Usage: /recorder <start [chamber] [seed] | stop | status>");
            return true;
        }

        switch (args[0].toLowerCase()) {
            case "start" -> {
                String chamber = args.length >= 2 ? args[1] : "unknown";
                long seed;
                try {
                    seed = args.length >= 3 ? Long.parseLong(args[2]) : 0L;
                } catch (NumberFormatException e) {
                    sender.sendMessage("Invalid seed '" + args[2] + "' — must be an integer.");
                    return true;
                }

                if (writer != null) {
                    sender.sendMessage("Already recording. /recorder stop first.");
                    return true;
                }

                Path episodesDir = resolveEpisodesDir();
                try {
                    writer = new EpisodeWriter(episodesDir, chamber, seed, getLogger());
                    listener.setWriter(writer, chamber, seed);
                    sender.sendMessage("Recorder started: " + writer.getFilePath().getFileName());
                } catch (IOException e) {
                    sender.sendMessage("Failed to open episode file: " + e.getMessage());
                    getLogger().severe("EpisodeWriter open failed: " + e.getMessage());
                }
            }

            case "stop" -> {
                if (writer == null) {
                    sender.sendMessage("Recorder is not running.");
                    return true;
                }
                String fileName = stopRecording();
                sender.sendMessage("Recorder stopped. Episode: " + fileName);
            }

            case "status" -> {
                if (writer == null) {
                    sender.sendMessage("Recorder: not recording.");
                } else {
                    sender.sendMessage("Recorder: active → " + writer.getFilePath());
                }
            }

            default -> sender.sendMessage("Unknown subcommand: " + args[0]);
        }

        return true;
    }

    // ─── Helpers ──────────────────────────────────────────────────────────────

    /** Flush pending actions, close the file, and clear state. Returns the file name. */
    private String stopRecording() {
        if (writer == null) return "(none)";
        if (listener != null) listener.flushAll();
        listener.setWriter(null, "unknown", 0L);
        String name = writer.getFilePath().getFileName().toString();
        writer.close();
        writer = null;
        return name;
    }

    /**
     * Resolve the episodes directory relative to the project root.
     *
     * <p>Layout:
     * <pre>
     *   &lt;repo-root&gt;/
     *     server/
     *       plugins/
     *         RecorderPlugin/   ← getDataFolder()
     *     episodes/             ← target
     * </pre>
     */
    private Path resolveEpisodesDir() {
        // getDataFolder() may be relative when Paper starts; resolve to absolute first
        // so that getParentFile() never returns null mid-chain.
        // Absolute chain: .../server/plugins/RecorderPlugin → plugins → server → repo-root
        return getDataFolder()
                .getAbsoluteFile()  // ensure absolute path
                .getParentFile()    // server/plugins
                .getParentFile()    // server
                .getParentFile()    // repo root
                .toPath()
                .resolve("episodes");
    }
}
