package com.minecraftrecorder;

import org.bukkit.Material;
import org.bukkit.entity.Player;
import org.bukkit.entity.Projectile;
import org.bukkit.event.EventHandler;
import org.bukkit.event.EventPriority;
import org.bukkit.event.Listener;
import org.bukkit.event.block.BlockBreakEvent;
import org.bukkit.event.entity.EntityDamageByEntityEvent;
import org.bukkit.event.entity.EntityDeathEvent;
import org.bukkit.event.inventory.CraftItemEvent;
import org.bukkit.event.inventory.InventoryCloseEvent;
import org.bukkit.event.inventory.InventoryOpenEvent;
import org.bukkit.event.inventory.InventoryType;
import org.bukkit.event.player.AsyncPlayerChatEvent;
import org.bukkit.inventory.Inventory;
import org.bukkit.inventory.ItemStack;
import org.bukkit.Location;
import org.bukkit.scheduler.BukkitRunnable;

import java.util.LinkedHashMap;
import java.util.Map;
import java.util.UUID;
import java.util.concurrent.ConcurrentHashMap;

/**
 * Server-side event listener that captures all player actions and writes them
 * as flat JSONL records via {@link EpisodeWriter}.
 *
 * <p>Action coverage:
 * <ul>
 *   <li><b>gather</b> — BlockBreakEvent (block broken by player)
 *   <li><b>combat</b> — EntityDamageByEntityEvent + EntityDeathEvent (FSM tracks start/kill)
 *   <li><b>transfer</b> — InventoryOpen + InventoryClose diff (items moved to/from container)
 *   <li><b>interact</b> — InventoryOpen + InventoryClose with no diff (chest viewed)
 *   <li><b>craft</b> — CraftItemEvent (item taken from crafting output)
 *   <li><b>say</b> — AsyncPlayerChatEvent
 *   <li><b>navigate</b> — injected automatically before any action when player moved &gt;4 blocks
 * </ul>
 */
public class PlayerRecorderListener implements Listener {

    // ─── State holders ────────────────────────────────────────────────────────

    private static class CombatInfo {
        final String entityType;
        final UUID   entityId;
        String       strategy;        // may upgrade from "melee" to "melee+shield" later
        final ObsSnapshot obs;
        final double tsStart;
        boolean shieldUsed = false;

        CombatInfo(String entityType, UUID entityId, String strategy,
                   ObsSnapshot obs, double tsStart) {
            this.entityType = entityType;
            this.entityId   = entityId;
            this.strategy   = strategy;
            this.obs        = obs;
            this.tsStart    = tsStart;
        }
    }

    private static class InventoryInfo {
        final String             containerType;
        final Inventory          container;
        final Map<String,Integer> snapshotAtOpen;
        final ObsSnapshot        obs;
        final double             tsOpen;

        InventoryInfo(String containerType, Inventory container,
                      Map<String,Integer> snapshotAtOpen, ObsSnapshot obs, double tsOpen) {
            this.containerType  = containerType;
            this.container      = container;
            this.snapshotAtOpen = snapshotAtOpen;
            this.obs            = obs;
            this.tsOpen         = tsOpen;
        }
    }

    // ─── Per-player state ─────────────────────────────────────────────────────

    private final Map<UUID, CombatInfo>    pendingCombat     = new ConcurrentHashMap<>();
    private final Map<UUID, InventoryInfo> pendingInventory  = new ConcurrentHashMap<>();
    private final Map<UUID, Location>      lastActionLoc     = new ConcurrentHashMap<>();
    private final Map<UUID, Double>        lastActionTime    = new ConcurrentHashMap<>();

    // ─── Active session ───────────────────────────────────────────────────────

    private volatile EpisodeWriter writer;
    private volatile String        chamber = "unknown";
    private volatile long          seed    = 0L;
    private final RecorderPlugin   plugin;

    public PlayerRecorderListener(RecorderPlugin plugin) {
        this.plugin = plugin;
        startCombatTimeoutTask();
    }

    public void setWriter(EpisodeWriter writer, String chamber, long seed) {
        this.writer  = writer;
        this.chamber = chamber;
        this.seed    = seed;
    }

    // ─── Guards ───────────────────────────────────────────────────────────────

    private boolean isRecording() { return writer != null; }

    private boolean shouldRecord(Player player) {
        return isRecording() && !player.getName().equals("Recorder");
    }

    // ─── Navigate injection ───────────────────────────────────────────────────

    /**
     * If the player has moved more than 4 blocks since the last action, emit a
     * navigate record first (with the last-action location as origin and current
     * location as destination). Then update the last-action location.
     */
    private void maybeWriteNavigate(Player player, ObsSnapshot actionObs, double actionTs) {
        UUID uid = player.getUniqueId();
        Location last = lastActionLoc.get(uid);
        if (last != null && last.getWorld() != null
                && last.getWorld().equals(player.getWorld())) {
            double dist = last.distance(player.getLocation());
            if (dist > 4.0) {
                double tsPrev = lastActionTime.getOrDefault(uid, actionTs - 5.0);
                String from = String.format("[%.2f,%.2f,%.2f]",
                        last.getX(), last.getY(), last.getZ());
                Location cur = player.getLocation();
                String to   = String.format("[%.2f,%.2f,%.2f]",
                        cur.getX(), cur.getY(), cur.getZ());
                String argsJson = "{\"from\":" + from + ",\"to\":" + to + "}";
                String result   = String.format("Moved from [%.0f,%.0f,%.0f] to [%.0f,%.0f,%.0f].",
                        last.getX(), last.getY(), last.getZ(),
                        cur.getX(), cur.getY(), cur.getZ());
                writer.write("navigate", argsJson, result, actionObs, tsPrev, actionTs);
            }
        }
        updateLastAction(player);
    }

    private void updateLastAction(Player player) {
        UUID uid = player.getUniqueId();
        lastActionLoc.put(uid, player.getLocation().clone());
        lastActionTime.put(uid, EpisodeWriter.nowSecs());
    }

    // ─── Event: gather (block break) ─────────────────────────────────────────

    @EventHandler(priority = EventPriority.MONITOR, ignoreCancelled = true)
    public void onBlockBreak(BlockBreakEvent event) {
        Player player = event.getPlayer();
        if (!shouldRecord(player)) return;

        double now = EpisodeWriter.nowSecs();
        ObsSnapshot obs = ObsSnapshot.capture(player);
        maybeWriteNavigate(player, obs, now);

        String blockType = event.getBlock().getType().getKey().getKey();
        String argsJson  = "{\"block_type\":\"" + ObsSnapshot.jsonEscape(blockType) + "\",\"count\":1}";
        String result    = "Mined 1\u00d7 " + blockType + ".";

        writer.write("gather", argsJson, result, obs, now, EpisodeWriter.nowSecs());
        updateLastAction(player);
    }

    // ─── Event: combat (attack start + kill) ─────────────────────────────────

    @EventHandler(priority = EventPriority.MONITOR, ignoreCancelled = true)
    public void onEntityDamageByEntity(EntityDamageByEntityEvent event) {
        // Determine attacker
        Player attacker = null;
        String strategy = "melee";

        if (event.getDamager() instanceof Player p) {
            attacker = p;
        } else if (event.getDamager() instanceof Projectile proj
                   && proj.getShooter() instanceof Player p) {
            attacker = p;
            strategy = "ranged";
        }

        // Track attacker start/update of combat
        if (attacker != null && shouldRecord(attacker)) {
            UUID uid = attacker.getUniqueId();
            UUID entityId   = event.getEntity().getUniqueId();
            String entityType = event.getEntity().getType().getKey().getKey();

            CombatInfo existing = pendingCombat.get(uid);
            if (existing == null) {
                // Start new combat
                ObsSnapshot obs = ObsSnapshot.capture(attacker);
                double now = EpisodeWriter.nowSecs();
                pendingCombat.put(uid, new CombatInfo(entityType, entityId, strategy, obs, now));
            } else if (!existing.entityId.equals(entityId)) {
                // Switched target — flush previous as partial then start new
                flushCombatPartial(attacker, existing);
                ObsSnapshot obs = ObsSnapshot.capture(attacker);
                double now = EpisodeWriter.nowSecs();
                pendingCombat.put(uid, new CombatInfo(entityType, entityId, strategy, obs, now));
            }
            // No else: same target, keep existing state (tsStart stays at first hit)
        }

        // Track victim: if player is blocking, mark shield used
        if (event.getEntity() instanceof Player victim && shouldRecord(victim)) {
            if (victim.isBlocking()) {
                CombatInfo ci = pendingCombat.get(victim.getUniqueId());
                if (ci != null) ci.shieldUsed = true;
            }
        }
    }

    @EventHandler(priority = EventPriority.MONITOR)
    public void onEntityDeath(EntityDeathEvent event) {
        Player killer = event.getEntity().getKiller();
        if (killer == null || !shouldRecord(killer)) return;

        UUID uid  = killer.getUniqueId();
        UUID eid  = event.getEntity().getUniqueId();
        CombatInfo ci = pendingCombat.get(uid);
        if (ci == null || !ci.entityId.equals(eid)) return;

        pendingCombat.remove(uid);
        double now = EpisodeWriter.nowSecs();

        String strat = ci.shieldUsed ? ci.strategy + "+shield" : ci.strategy;
        String argsJson = "{\"target_entity\":\"" + ObsSnapshot.jsonEscape(ci.entityType)
                        + "\",\"strategy\":\"" + strat + "\"}";
        String result   = "Killed " + ci.entityType + ".";

        maybeWriteNavigate(killer, ci.obs, ci.tsStart);
        writer.write("combat", argsJson, result, ci.obs, ci.tsStart, now);
        updateLastAction(killer);
    }

    // ─── Event: interact / transfer (chest & containers) ─────────────────────

    @EventHandler(priority = EventPriority.MONITOR, ignoreCancelled = true)
    public void onInventoryOpen(InventoryOpenEvent event) {
        if (!(event.getPlayer() instanceof Player player)) return;
        if (!shouldRecord(player)) return;

        String containerType = resolveContainerType(event.getInventory().getType());
        if (containerType == null) return; // skip crafting / own inventory / etc.

        Map<String, Integer> snapshot = inventoryContents(event.getInventory());
        ObsSnapshot obs = ObsSnapshot.capture(player);
        pendingInventory.put(player.getUniqueId(),
                new InventoryInfo(containerType, event.getInventory(), snapshot, obs,
                                  EpisodeWriter.nowSecs()));
    }

    @EventHandler(priority = EventPriority.MONITOR)
    public void onInventoryClose(InventoryCloseEvent event) {
        if (!(event.getPlayer() instanceof Player player)) return;
        if (!shouldRecord(player)) return;

        InventoryInfo info = pendingInventory.remove(player.getUniqueId());
        if (info == null) return;

        double now = EpisodeWriter.nowSecs();
        Map<String, Integer> after = inventoryContents(info.container);

        // Diff
        Map<String, Integer> taken  = new LinkedHashMap<>();  // removed from container
        Map<String, Integer> placed = new LinkedHashMap<>();  // added to container

        for (var entry : info.snapshotAtOpen.entrySet()) {
            int afterAmt = after.getOrDefault(entry.getKey(), 0);
            if (afterAmt < entry.getValue())
                taken.put(entry.getKey(), entry.getValue() - afterAmt);
        }
        for (var entry : after.entrySet()) {
            int beforeAmt = info.snapshotAtOpen.getOrDefault(entry.getKey(), 0);
            if (entry.getValue() > beforeAmt)
                placed.put(entry.getKey(), entry.getValue() - beforeAmt);
        }

        maybeWriteNavigate(player, info.obs, info.tsOpen);

        if (!taken.isEmpty() || !placed.isEmpty()) {
            // Items moved — write transfer action
            String direction = (!taken.isEmpty() && placed.isEmpty()) ? "take"
                             : (taken.isEmpty())                       ? "put"
                             : "both";
            Map<String, Integer> primary = taken.isEmpty() ? placed : taken;
            writer.write("transfer", buildTransferArgs(direction, info.containerType, primary),
                         buildTransferResult(direction, taken, placed, info.containerType),
                         info.obs, info.tsOpen, now);
        } else {
            // Just viewed — write interact action
            String contentsStr = describeContents(info.snapshotAtOpen);
            String argsJson  = "{\"target\":\"" + info.containerType + "\"}";
            String result    = info.snapshotAtOpen.isEmpty()
                    ? "Opened empty " + info.containerType + "."
                    : "Inspected " + info.containerType + ": " + contentsStr + ".";
            writer.write("interact", argsJson, result, info.obs, info.tsOpen, now);
        }

        updateLastAction(player);
    }

    // ─── Event: craft ─────────────────────────────────────────────────────────

    @EventHandler(priority = EventPriority.MONITOR, ignoreCancelled = true)
    public void onCraftItem(CraftItemEvent event) {
        if (!(event.getWhoClicked() instanceof Player player)) return;
        if (!shouldRecord(player)) return;

        ItemStack result = event.getRecipe().getResult();
        if (result.getType() == Material.AIR) return;

        double now = EpisodeWriter.nowSecs();
        ObsSnapshot obs = ObsSnapshot.capture(player);
        String itemName = result.getType().getKey().getKey();

        // Determine count (shift-click produces a full batch)
        int count = event.isShiftClick()
                ? calculateMaxCraftAmount(event)
                : result.getAmount();

        String argsJson = "{\"item\":\"" + ObsSnapshot.jsonEscape(itemName)
                        + "\",\"count\":" + count + "}";
        String res = "Crafted " + count + "\u00d7 " + itemName + ".";

        maybeWriteNavigate(player, obs, now);
        writer.write("craft", argsJson, res, obs, now, EpisodeWriter.nowSecs());
        updateLastAction(player);
    }

    // ─── Event: say ───────────────────────────────────────────────────────────

    @EventHandler(priority = EventPriority.MONITOR, ignoreCancelled = true)
    public void onPlayerChat(AsyncPlayerChatEvent event) {
        Player player = event.getPlayer();
        if (!shouldRecord(player)) return;

        final String message = event.getMessage();
        final double now = EpisodeWriter.nowSecs();

        // Chat fires async — capture obs on main thread via scheduler
        plugin.getServer().getScheduler().runTask(plugin, () -> {
            if (!shouldRecord(player)) return;
            ObsSnapshot obs = ObsSnapshot.capture(player);
            String argsJson = "{\"message\":\"" + ObsSnapshot.jsonEscape(message) + "\"}";
            String result   = "Said: '" + ObsSnapshot.jsonEscape(message) + "'.";
            maybeWriteNavigate(player, obs, now);
            writer.write("say", argsJson, result, obs, now, EpisodeWriter.nowSecs());
            updateLastAction(player);
        });
    }

    // ─── Combat timeout task ──────────────────────────────────────────────────

    /**
     * Every 5 seconds, flush any combat states older than 15 seconds as partial
     * (entity survived). Prevents dangling combat state if player runs away.
     */
    private void startCombatTimeoutTask() {
        new BukkitRunnable() {
            @Override public void run() {
                if (!isRecording()) return;
                double now = EpisodeWriter.nowSecs();
                for (var entry : pendingCombat.entrySet()) {
                    CombatInfo ci = entry.getValue();
                    if (now - ci.tsStart > 15.0) {
                        Player player = plugin.getServer().getPlayer(entry.getKey());
                        if (player != null) flushCombatPartial(player, ci);
                        else flushCombatPartialNoPlayer(entry.getKey(), ci);
                    }
                }
            }
        }.runTaskTimer(plugin, 100L, 100L); // every 5 s (20 ticks/s)
    }

    private void flushCombatPartial(Player player, CombatInfo ci) {
        pendingCombat.remove(player.getUniqueId());
        double now   = EpisodeWriter.nowSecs();
        String strat = ci.shieldUsed ? ci.strategy + "+shield" : ci.strategy;
        String argsJson = "{\"target_entity\":\"" + ObsSnapshot.jsonEscape(ci.entityType)
                        + "\",\"strategy\":\"" + strat + "\"}";
        String result   = "Attacked " + ci.entityType + " (did not kill).";
        writer.write("combat", argsJson, result, ci.obs, ci.tsStart, now);
        updateLastAction(player);
    }

    private void flushCombatPartialNoPlayer(UUID uid, CombatInfo ci) {
        pendingCombat.remove(uid);
        double now   = EpisodeWriter.nowSecs();
        String strat = ci.shieldUsed ? ci.strategy + "+shield" : ci.strategy;
        String argsJson = "{\"target_entity\":\"" + ObsSnapshot.jsonEscape(ci.entityType)
                        + "\",\"strategy\":\"" + strat + "\"}";
        writer.write("combat", argsJson, "Attacked " + ci.entityType + " (did not kill).",
                     ci.obs, ci.tsStart, now);
    }

    /** Flush any pending states (called on recorder stop). */
    public void flushAll() {
        for (var entry : new java.util.ArrayList<>(pendingCombat.entrySet())) {
            flushCombatPartialNoPlayer(entry.getKey(), entry.getValue());
        }
        // Pending inventory interactions are discarded on stop (incomplete session).
        pendingInventory.clear();
    }

    // ─── Helpers ──────────────────────────────────────────────────────────────

    private static Map<String, Integer> inventoryContents(Inventory inv) {
        Map<String, Integer> map = new LinkedHashMap<>();
        for (ItemStack item : inv.getContents()) {
            if (item != null && item.getType() != Material.AIR) {
                map.merge(item.getType().getKey().getKey(), item.getAmount(), Integer::sum);
            }
        }
        return map;
    }

    /**
     * Map InventoryType to a display name, or null to skip recording.
     * We only record external containers (chests, furnaces, etc.), not the
     * player's own inventory or workbench (craft event handles crafting).
     */
    private static String resolveContainerType(InventoryType type) {
        return switch (type) {
            case CHEST, BARREL, SHULKER_BOX -> "chest";
            case ENDER_CHEST                 -> "ender_chest";
            case FURNACE, BLAST_FURNACE,
                 SMOKER                      -> "furnace";
            case HOPPER                      -> "hopper";
            case DISPENSER, DROPPER          -> "dispenser";
            default                          -> null; // CRAFTING, WORKBENCH, PLAYER, etc.
        };
    }

    private static String buildTransferArgs(String direction, String container,
                                            Map<String, Integer> items) {
        StringBuilder sb = new StringBuilder("{\"direction\":\"");
        sb.append(direction).append("\",\"container\":\"").append(container)
          .append("\",\"items\":{");
        boolean first = true;
        for (var e : items.entrySet()) {
            if (!first) sb.append(",");
            sb.append("\"").append(ObsSnapshot.jsonEscape(e.getKey())).append("\":")
              .append(e.getValue());
            first = false;
        }
        sb.append("}}");
        return sb.toString();
    }

    private static String buildTransferResult(String direction,
                                              Map<String, Integer> taken,
                                              Map<String, Integer> placed,
                                              String container) {
        StringBuilder sb = new StringBuilder();
        if (!taken.isEmpty()) {
            sb.append("Took ").append(describeItems(taken)).append(" from ").append(container);
        }
        if (!placed.isEmpty()) {
            if (!sb.isEmpty()) sb.append("; ");
            sb.append("Put ").append(describeItems(placed)).append(" into ").append(container);
        }
        sb.append(".");
        return sb.toString();
    }

    private static String describeItems(Map<String, Integer> items) {
        StringBuilder sb = new StringBuilder();
        boolean first = true;
        for (var e : items.entrySet()) {
            if (!first) sb.append(", ");
            sb.append(e.getKey()).append("\u00d7").append(e.getValue());
            first = false;
        }
        return sb.toString();
    }

    private static String describeContents(Map<String, Integer> contents) {
        if (contents.isEmpty()) return "{}";
        StringBuilder sb = new StringBuilder("{");
        boolean first = true;
        for (var e : contents.entrySet()) {
            if (!first) sb.append(", ");
            sb.append(e.getKey()).append(": ").append(e.getValue());
            first = false;
        }
        sb.append("}");
        return sb.toString();
    }

    private static int calculateMaxCraftAmount(CraftItemEvent event) {
        // Estimate batch size for shift-click: minimum of (ingredient stacks / recipe cost)
        // Simple approximation: just use result item amount * 8 (one full batch)
        return event.getRecipe().getResult().getAmount() * 8;
    }
}
