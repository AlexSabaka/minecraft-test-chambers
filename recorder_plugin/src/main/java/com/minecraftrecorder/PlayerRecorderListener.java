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
import org.bukkit.event.block.BlockDamageEvent;
import org.bukkit.event.player.AsyncPlayerChatEvent;
import org.bukkit.event.player.PlayerDropItemEvent;
import org.bukkit.event.player.PlayerInteractEvent;
import org.bukkit.event.player.PlayerItemConsumeEvent;
import org.bukkit.event.player.PlayerQuitEvent;
import org.bukkit.event.player.PlayerSwapHandItemsEvent;
import com.destroystokyo.paper.event.player.PlayerJumpEvent;
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
 *   <li><b>consume</b> — PlayerItemConsumeEvent (food, potion, milk bucket, etc.)
 *   <li><b>say</b> — AsyncPlayerChatEvent
 *   <li><b>navigate</b> — injected before discrete events when player moved &gt;4 blocks; also
 *       emitted by the fixed-interval tick when player moves &gt;1 block per 500 ms
 *   <li><b>take_damage</b> — tick-based: health dropped ≥ 1.0 between ticks
 *   <li><b>heal</b> — tick-based: health increased ≥ 1.0 with no food change (regen / golden apple)
 *   <li><b>idle</b> — tick-based: no significant change; provides a 500 ms observation heartbeat
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

    private static final class GatherPool {
        final String blockType;
        int count;
        final ObsSnapshot obs;  // snapshot at first break — used as ts_start obs
        final double tsStart;
        double tsEnd;

        GatherPool(String blockType, ObsSnapshot obs, double ts) {
            this.blockType = blockType;
            this.count     = 1;
            this.obs       = obs;
            this.tsStart   = ts;
            this.tsEnd     = ts;
        }

        void accumulate(double ts) { count++; tsEnd = ts; }
    }

    // ─── Per-player state ─────────────────────────────────────────────────────

    private final Map<UUID, CombatInfo>    pendingCombat     = new ConcurrentHashMap<>();
    private final Map<UUID, InventoryInfo> pendingInventory  = new ConcurrentHashMap<>();
    private final Map<UUID, GatherPool>    pendingGather     = new ConcurrentHashMap<>();
    private final Map<UUID, Location>      lastActionLoc     = new ConcurrentHashMap<>();
    private final Map<UUID, Double>        lastActionTime    = new ConcurrentHashMap<>();

    // ─── Recording mode ──────────────────────────────────────────────────────────

    /** {@code true} when recording in MineRL control-space format. */
    private volatile boolean mineRLMode = false;

    // ─── Semantic tick recorder state ──────────────────────────────────

    private final Map<UUID, Location> tickPrevLoc    = new ConcurrentHashMap<>();
    private final Map<UUID, Double>   tickPrevHealth = new ConcurrentHashMap<>();
    private final Map<UUID, Integer>  tickPrevFood   = new ConcurrentHashMap<>();

    // ─── MineRL per-tick input flags ────────────────────────────────────────
    // Set by event handlers; read and cleared each 500 ms tick.

    private final Map<UUID, Integer> mrlAttackCount  = new ConcurrentHashMap<>();
    private final Map<UUID, Boolean> mrlUse          = new ConcurrentHashMap<>();
    private final Map<UUID, Boolean> mrlJump         = new ConcurrentHashMap<>();
    private final Map<UUID, Boolean> mrlDrop         = new ConcurrentHashMap<>();
    private final Map<UUID, Boolean> mrlSwapHands    = new ConcurrentHashMap<>();
    private final Map<UUID, Boolean> mrlInvOpen      = new ConcurrentHashMap<>();
    private final Map<UUID, Float>   mrlPrevYaw      = new ConcurrentHashMap<>();
    private final Map<UUID, Float>   mrlPrevPitch    = new ConcurrentHashMap<>();

    // ─── Active session ───────────────────────────────────────────────────────

    private volatile EpisodeWriter writer;
    private volatile String        chamber = "unknown";
    private volatile long          seed    = 0L;
    private final RecorderPlugin   plugin;

    public PlayerRecorderListener(RecorderPlugin plugin) {
        this.plugin = plugin;
        startCombatTimeoutTask();
        startTickTask();
        startMineRLTickTask();
    }

    public void setWriter(EpisodeWriter writer, String chamber, long seed, String format) {
        this.writer    = writer;
        this.chamber   = chamber;
        this.seed      = seed;
        this.mineRLMode = "minerl".equals(format);
        if (writer != null) {
            // Seed baseline state so first tick has valid deltas
            for (Player player : plugin.getServer().getOnlinePlayers()) {
                UUID uid = player.getUniqueId();
                tickPrevLoc.put(uid, player.getLocation().clone());
                tickPrevHealth.put(uid, player.getHealth());
                tickPrevFood.put(uid, player.getFoodLevel());
                mrlPrevYaw.put(uid, player.getLocation().getYaw());
                mrlPrevPitch.put(uid, player.getLocation().getPitch());
            }
        } else {
            tickPrevLoc.clear();
            tickPrevHealth.clear();
            tickPrevFood.clear();
            mrlPrevYaw.clear();
            mrlPrevPitch.clear();
        }
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
        flushGatherPool(uid);  // flush any pending gather before this action
        Location last = lastActionLoc.get(uid);
        if (last != null && last.getWorld() != null
                && last.getWorld().equals(player.getWorld())) {
            double dist = last.distance(player.getLocation());
            if (dist > 1.0) {
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
        if (mineRLMode) return;

        UUID uid         = player.getUniqueId();
        double now       = EpisodeWriter.nowSecs();
        String blockType = event.getBlock().getType().getKey().getKey();

        GatherPool existing = pendingGather.get(uid);
        if (existing != null && existing.blockType.equals(blockType)) {
            // Same block type: accumulate silently, no write yet
            existing.accumulate(now);
            updateLastAction(player);
            return;
        }

        // Block type changed or first break — flush old pool, inject navigate, start new pool
        ObsSnapshot obs = ObsSnapshot.capture(player);
        maybeWriteNavigate(player, obs, now);  // also flushes old gather pool
        pendingGather.put(uid, new GatherPool(blockType, obs, now));
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

            if (mineRLMode) {
                // In MineRL mode just set the attack flag; no FSM.
                mrlAttackCount.merge(uid, 1, Integer::sum);
                return;
            }

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
        if (mineRLMode) return;

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

        if (mineRLMode) {
            mrlInvOpen.put(player.getUniqueId(), Boolean.TRUE);
            return;
        }

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

        if (mineRLMode) {
            mrlInvOpen.put(player.getUniqueId(), Boolean.FALSE);
            return;
        }

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
        if (mineRLMode) return;

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

    // ─── Event: consume (eat / drink / potion) ──────────────────────────────────

    /**
     * Fires when a player finishes consuming an item (food, potion, milk bucket, etc.).
     * This catches cases the tick recorder cannot: the exact item consumed and the moment
     * it completes (e.g. golden apple, suspicious stew, potions).
     */
    @EventHandler(priority = EventPriority.MONITOR, ignoreCancelled = true)
    public void onPlayerItemConsume(PlayerItemConsumeEvent event) {
        Player player = event.getPlayer();
        if (!shouldRecord(player)) return;
        if (mineRLMode) {
            // Consuming an item requires holding use; set the flag.
            mrlUse.put(player.getUniqueId(), Boolean.TRUE);
            return;
        }

        double now = EpisodeWriter.nowSecs();
        ObsSnapshot obs = ObsSnapshot.capture(player);
        String itemName = event.getItem().getType().getKey().getKey();
        String argsJson = "{\"item\":\"" + ObsSnapshot.jsonEscape(itemName) + "\",\"count\":1}";
        String result   = "Consumed " + itemName + ".";

        maybeWriteNavigate(player, obs, now);
        writer.write("consume", argsJson, result, obs, now, EpisodeWriter.nowSecs());
        updateLastAction(player);
    }

    // ─── Event: say ───────────────────────────────────────────────────────────

    @EventHandler(priority = EventPriority.MONITOR, ignoreCancelled = true)
    public void onPlayerChat(AsyncPlayerChatEvent event) {
        Player player = event.getPlayer();
        if (!shouldRecord(player)) return;
        if (mineRLMode) return;

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

    // ─── MineRL event flags ─────────────────────────────────────────────────────────

    /** LMB held on a block. Sets {@code attack} flag for current tick window. */
    @EventHandler(priority = EventPriority.MONITOR, ignoreCancelled = true)
    public void onBlockDamage(BlockDamageEvent event) {
        Player player = event.getPlayer();
        if (!shouldRecord(player) || !mineRLMode) return;
        mrlAttackCount.merge(player.getUniqueId(), 1, Integer::sum);
    }

    /** Right-click on block / air. Sets {@code use} flag. */
    @EventHandler(priority = EventPriority.MONITOR)
    public void onPlayerInteract(PlayerInteractEvent event) {
        if (!(event.getPlayer() instanceof Player player)) return;
        if (!shouldRecord(player) || !mineRLMode) return;
        mrlUse.put(player.getUniqueId(), Boolean.TRUE);
    }

    /** Player presses Space. Sets {@code jump} flag. */
    @EventHandler(priority = EventPriority.MONITOR)
    public void onPlayerJump(PlayerJumpEvent event) {
        Player player = event.getPlayer();
        if (!shouldRecord(player) || !mineRLMode) return;
        mrlJump.put(player.getUniqueId(), Boolean.TRUE);
    }

    /** Player drops an item (Q). Sets {@code drop} flag. */
    @EventHandler(priority = EventPriority.MONITOR, ignoreCancelled = true)
    public void onPlayerDropItem(PlayerDropItemEvent event) {
        Player player = event.getPlayer();
        if (!shouldRecord(player) || !mineRLMode) return;
        mrlDrop.put(player.getUniqueId(), Boolean.TRUE);
    }

    /** Player presses F to swap main/off-hand. Sets {@code swapHands} flag. */
    @EventHandler(priority = EventPriority.MONITOR, ignoreCancelled = true)
    public void onPlayerSwapHandItems(PlayerSwapHandItemsEvent event) {
        Player player = event.getPlayer();
        if (!shouldRecord(player) || !mineRLMode) return;
        mrlSwapHands.put(player.getUniqueId(), Boolean.TRUE);
    }

    // ─── Player disconnect cleanup ────────────────────────────────────────────

    /** Remove all per-player state when a player leaves to prevent memory leaks. */
    @EventHandler
    public void onPlayerQuit(PlayerQuitEvent event) {
        UUID uid = event.getPlayer().getUniqueId();
        if (isRecording()) flushGatherPool(uid);  // write any pending gather before cleanup
        pendingCombat.remove(uid);
        pendingInventory.remove(uid);
        pendingGather.remove(uid);
        lastActionLoc.remove(uid);
        lastActionTime.remove(uid);
        tickPrevLoc.remove(uid);
        tickPrevHealth.remove(uid);
        tickPrevFood.remove(uid);
        mrlAttackCount.remove(uid);
        mrlUse.remove(uid);
        mrlJump.remove(uid);
        mrlDrop.remove(uid);
        mrlSwapHands.remove(uid);
        mrlInvOpen.remove(uid);
        mrlPrevYaw.remove(uid);
        mrlPrevPitch.remove(uid);
    }

    // ─── Fixed-interval tick recorder (semantic) ───────────────────────────────

    /**
     * Every 10 server ticks (≈ 500 ms at 20 TPS), emit one action record per
     * recorded player — unless an event-driven record was written very recently.
     *
     * <p>Label priority:
     * <ol>
     *   <li><b>navigate</b> — player moved &gt; 1 block since last tick
     *   <li><b>take_damage</b> — health dropped ≥ 1.0 since last tick
     *   <li><b>heal</b> — health increased ≥ 1.0 and food level unchanged
     *   <li><b>idle</b> — nothing significant changed (obs snapshot only)
     * </ol>
     *
     * <p>The tick fills the gaps between event-driven records so that every
     * screenshot frame in the merged episode has a recent, accurate action.
     */
    private void startTickTask() {
        new BukkitRunnable() {
            @Override public void run() {
                if (!isRecording() || mineRLMode) return;
                double now = EpisodeWriter.nowSecs();
                for (Player player : plugin.getServer().getOnlinePlayers()) {
                    if (!shouldRecord(player)) continue;
                    UUID uid = player.getUniqueId();
                    // Skip if an event-driven record was written < 400 ms ago
                    double lastWritten = lastActionTime.getOrDefault(uid, 0.0);
                    if (now - lastWritten < 0.4) continue;
                    writeTick(player, uid, now);
                }
            }
        }.runTaskTimer(plugin, 10L, 10L); // 10 ticks = 500 ms
    }

    // ─── Fixed-interval tick recorder (MineRL) ──────────────────────────────

    /**
     * Every 10 server ticks (≈500 ms), emit one MineRL control-space record per
     * recorded player.  Reads and clears the per-player event flags accumulated
     * since the previous tick, then writes a {@code controls} dict together with
     * the current obs snapshot.
     *
     * <p>Movement (forward/back/left/right) is inferred from the XZ position
     * delta decomposed against the player’s current yaw.
     * Camera (yaw/pitch) delta is tracked from the previous tick.
     */
    private void startMineRLTickTask() {
        new BukkitRunnable() {
            @Override public void run() {
                if (!isRecording() || !mineRLMode) return;
                double now = EpisodeWriter.nowSecs();
                for (Player player : plugin.getServer().getOnlinePlayers()) {
                    if (!shouldRecord(player)) continue;
                    writeMineRLTick(player, player.getUniqueId(), now);
                }
            }
        }.runTaskTimer(plugin, 10L, 10L); // 10 ticks = 500 ms
    }

    private void writeMineRLTick(Player player, UUID uid, double now) {
        ObsSnapshot obs  = ObsSnapshot.capture(player);
        Location    cur  = player.getLocation();
        float       yaw  = cur.getYaw();
        float       pitch = cur.getPitch();

        // ─ Movement decomposition ───────────────────────────────────────
        // Minecraft yaw: 0°=South(+Z), 90°=West(-X), −90°=East(+X), 180°=North(-Z)
        // Forward unit vector in (X, Z): (-sin(yaw), cos(yaw))
        // Strafe-right unit vector in (X, Z): (cos(yaw), sin(yaw))
        Location prevLoc = tickPrevLoc.get(uid);
        int forward = 0, back = 0, left = 0, right = 0;
        if (prevLoc != null && prevLoc.getWorld() != null
                && prevLoc.getWorld().equals(cur.getWorld())) {
            double dx = cur.getX() - prevLoc.getX();
            double dz = cur.getZ() - prevLoc.getZ();
            double yawRad = Math.toRadians(yaw);
            double fwdDot  = dx * -Math.sin(yawRad) + dz *  Math.cos(yawRad);
            double rtDot   = dx *  Math.cos(yawRad) + dz *  Math.sin(yawRad);
            if (fwdDot >  0.05) forward = 1;
            if (fwdDot < -0.05) back    = 1;
            if (rtDot  < -0.05) left    = 1;  // strafe left = rtDot negative
            if (rtDot  >  0.05) right   = 1;
        }

        // ─ Camera delta ────────────────────────────────────────────────
        float prevYaw   = mrlPrevYaw.getOrDefault(uid, yaw);
        float prevPitch = mrlPrevPitch.getOrDefault(uid, pitch);
        float deltaYaw  = yaw - prevYaw;
        float deltaPitch = pitch - prevPitch;
        // Wrap yaw delta to (-180, +180]
        while (deltaYaw >  180f) deltaYaw -= 360f;
        while (deltaYaw < -180f) deltaYaw += 360f;

        // ─ Event flags (read + clear) ───────────────────────────────────
        int attack    = mrlAttackCount.getOrDefault(uid, 0);
        mrlAttackCount.remove(uid);
        int use       = Boolean.TRUE.equals(mrlUse.remove(uid))       ? 1 : 0;
        int jump      = Boolean.TRUE.equals(mrlJump.remove(uid))      ? 1 : 0;
        int drop      = Boolean.TRUE.equals(mrlDrop.remove(uid))      ? 1 : 0;
        int swapHands = Boolean.TRUE.equals(mrlSwapHands.remove(uid)) ? 1 : 0;
        int inventory = Boolean.TRUE.equals(mrlInvOpen.get(uid))      ? 1 : 0;

        // ─ Polled state ─────────────────────────────────────────────────
        int sneak  = player.isSneaking()  ? 1 : 0;
        int sprint = player.isSprinting() ? 1 : 0;
        int slot   = player.getInventory().getHeldItemSlot(); // 0-8

        // ─ Build hotbar one-hot ───────────────────────────────────────────
        StringBuilder sb = new StringBuilder();
        sb.append("{");
        sb.append("\"ESC\":0");  // not observable
        sb.append(",\"attack\":").append(attack);
        sb.append(",\"back\":").append(back);
        sb.append(",\"camera\":[")
          .append(String.format("%.2f", deltaYaw)).append(",")
          .append(String.format("%.2f", deltaPitch)).append("]");
        sb.append(",\"drop\":").append(drop);
        sb.append(",\"forward\":").append(forward);
        for (int i = 1; i <= 9; i++) {
            sb.append(",\"hotbar.").append(i).append("\": ").append(slot == i - 1 ? 1 : 0);
        }
        sb.append(",\"inventory\":").append(inventory);
        sb.append(",\"jump\":").append(jump);
        sb.append(",\"left\":").append(left);
        sb.append(",\"pickItem\":0");  // not observable
        sb.append(",\"right\":").append(right);
        sb.append(",\"sneak\":").append(sneak);
        sb.append(",\"sprint\":").append(sprint);
        sb.append(",\"swapHands\":").append(swapHands);
        sb.append(",\"use\":").append(use);
        sb.append("}");

        // Update baseline
        tickPrevLoc.put(uid, cur.clone());
        mrlPrevYaw.put(uid, yaw);
        mrlPrevPitch.put(uid, pitch);

        writer.writeMineRL(sb.toString(), obs, now);
    }

    private void writeTick(Player player, UUID uid, double now) {
        flushGatherPool(uid);  // flush any pending gather before the tick record
        ObsSnapshot obs = ObsSnapshot.capture(player);
        Location cur    = player.getLocation();

        Location prevLoc    = tickPrevLoc.get(uid);
        double   prevHealth = tickPrevHealth.getOrDefault(uid, obs.health);
        int      prevFood   = tickPrevFood.getOrDefault(uid, obs.hunger);

        // Update baseline for next tick
        tickPrevLoc.put(uid, cur.clone());
        tickPrevHealth.put(uid, obs.health);
        tickPrevFood.put(uid, obs.hunger);

        String action;
        String argsJson;
        String result;

        if (prevLoc != null && prevLoc.getWorld() != null
                && prevLoc.getWorld().equals(cur.getWorld())) {
            double dist        = prevLoc.distance(cur);
            double healthDelta = obs.health - prevHealth;

            if (dist > 1.0) {
                String from = String.format("[%.2f,%.2f,%.2f]",
                        prevLoc.getX(), prevLoc.getY(), prevLoc.getZ());
                String to   = String.format("[%.2f,%.2f,%.2f]",
                        cur.getX(), cur.getY(), cur.getZ());
                argsJson = "{\"from\":" + from + ",\"to\":" + to
                         + ",\"distance\":" + String.format("%.2f", dist) + "}";
                result = String.format("Moved %.1f blocks to [%.0f,%.0f,%.0f].",
                        dist, cur.getX(), cur.getY(), cur.getZ());
                action = "navigate";
            } else if (healthDelta <= -1.0) {
                argsJson = String.format("{\"damage\":%.1f}", -healthDelta);
                result   = String.format("Took %.1f damage.", -healthDelta);
                action   = "take_damage";
            } else if (healthDelta >= 1.0 && obs.hunger == prevFood) {
                // Health recovered but food unchanged → regen / golden apple effect
                argsJson = String.format("{\"amount\":%.1f}", healthDelta);
                result   = String.format("Healed %.1f health.", healthDelta);
                action   = "heal";
            } else {
                argsJson = "{}";
                result   = "Idle.";
                action   = "idle";
            }
        } else {
            // First tick for this player (no previous location yet)
            argsJson = "{}";
            result   = "Idle.";
            action   = "idle";
        }

        writer.write(action, argsJson, result, obs, now, EpisodeWriter.nowSecs());
        updateLastAction(player);
    }

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

    private void flushGatherPool(UUID uid) {
        GatherPool pool = pendingGather.remove(uid);
        if (pool == null || writer == null) return;
        String argsJson = "{\"block_type\":\"" + ObsSnapshot.jsonEscape(pool.blockType)
                        + "\",\"count\":" + pool.count + "}";
        String result = "Mined " + pool.count + "\u00d7 " + pool.blockType + ".";
        writer.write("gather", argsJson, result, pool.obs, pool.tsStart, pool.tsEnd);
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
        // Flush any pending gather pools
        for (UUID uid : new java.util.ArrayList<>(pendingGather.keySet())) {
            flushGatherPool(uid);
        }
        // Pending inventory interactions are discarded on stop (incomplete session).
        pendingInventory.clear();
        // Clear tick baseline state
        tickPrevLoc.clear();
        tickPrevHealth.clear();
        tickPrevFood.clear();
        // Clear MineRL flag maps
        mrlAttackCount.clear();
        mrlUse.clear();
        mrlJump.clear();
        mrlDrop.clear();
        mrlSwapHands.clear();
        mrlInvOpen.clear();
        mrlPrevYaw.clear();
        mrlPrevPitch.clear();
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
