package com.minecraftrecorder;

import java.io.BufferedWriter;
import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.time.Instant;
import java.time.ZoneOffset;
import java.time.format.DateTimeFormatter;
import java.util.logging.Logger;

/**
 * Thread-safe writer for flat-JSONL episode files.
 *
 * <p>Each call to {@link #write} appends one record as a single JSON line.
 * The output file is created under {@code episodesDir} with the name pattern:
 * <pre>{chamber}_{seed}_{yyyyMMdd'T'HHmmss'Z'}.jsonl</pre>
 */
public final class EpisodeWriter implements AutoCloseable {

    private static final DateTimeFormatter TIMESTAMP_FMT =
            DateTimeFormatter.ofPattern("yyyyMMdd'T'HHmmss'Z'")
                             .withZone(ZoneOffset.UTC);

    private final Path filePath;
    private final String chamber;
    private final long seed;
    private final BufferedWriter writer;
    private final Logger logger;

    public EpisodeWriter(Path episodesDir, String chamber, long seed, Logger logger)
            throws IOException {
        this.chamber = chamber;
        this.seed    = seed;
        this.logger  = logger;

        Files.createDirectories(episodesDir);
        String timestamp = TIMESTAMP_FMT.format(Instant.now());
        String filename  = chamber + "_" + seed + "_" + timestamp + ".jsonl";
        this.filePath = episodesDir.resolve(filename);
        this.writer   = Files.newBufferedWriter(filePath, StandardCharsets.UTF_8);
        logger.info("[Recorder] Writing episode to: " + filePath);
    }

    /**
     * Write one action record as a JSON line.
     *
     * @param action    e.g. "gather"
     * @param argsJson  pre-built JSON object string for args, e.g. {"block_type":"gold_ore","count":1}
     * @param result    human-readable outcome string
     * @param obs       player observation snapshot at action start
     * @param tsStart   epoch seconds (double)
     * @param tsEnd     epoch seconds (double)
     */
    public synchronized void write(String action, String argsJson, String result,
                                   ObsSnapshot obs, double tsStart, double tsEnd) {
        try {
            StringBuilder sb = new StringBuilder();
            sb.append("{");
            sb.append("\"action\":\"").append(ObsSnapshot.jsonEscape(action)).append("\"");
            sb.append(",\"args\":").append(argsJson);
            sb.append(",\"result\":\"").append(ObsSnapshot.jsonEscape(result)).append("\"");
            sb.append(",\"obs\":").append(obs.toJson());
            sb.append(",\"ts_start\":").append(String.format("%.3f", tsStart));
            sb.append(",\"ts_end\":").append(String.format("%.3f", tsEnd));
            sb.append(",\"chamber\":\"").append(ObsSnapshot.jsonEscape(chamber)).append("\"");
            sb.append(",\"seed\":").append(seed);
            sb.append("}");
            writer.write(sb.toString());
            writer.newLine();
            writer.flush();
        } catch (IOException e) {
            logger.warning("[Recorder] Failed to write record: " + e.getMessage());
        }
    }

    public Path getFilePath() { return filePath; }

    @Override
    public synchronized void close() {
        try {
            writer.close();
            logger.info("[Recorder] Episode closed: " + filePath.getFileName());
        } catch (IOException e) {
            logger.warning("[Recorder] Error closing episode file: " + e.getMessage());
        }
    }

    /** Epoch seconds as a double, matching Python's time.time(). */
    public static double nowSecs() {
        return System.currentTimeMillis() / 1000.0;
    }
}
