package com.minecraftrecorder;

import org.bukkit.Material;
import org.bukkit.block.BlockFace;
import org.bukkit.entity.Player;
import org.bukkit.inventory.ItemStack;
import org.bukkit.inventory.PlayerInventory;

import java.util.LinkedHashMap;
import java.util.Map;

/**
 * Immutable snapshot of a player's observable state at a point in time.
 */
public final class ObsSnapshot {

    public final double posX, posY, posZ;
    public final String facing;
    public final double health;
    public final int hunger;
    public final String held;
    public final Map<String, Integer> inv;
    public final int xp;

    private ObsSnapshot(double posX, double posY, double posZ, String facing,
                        double health, int hunger, String held,
                        Map<String, Integer> inv, int xp) {
        this.posX   = posX;
        this.posY   = posY;
        this.posZ   = posZ;
        this.facing = facing;
        this.health = health;
        this.hunger = hunger;
        this.held   = held;
        this.inv    = inv;
        this.xp     = xp;
    }

    /** Capture the current state of a player. Must be called on the main thread. */
    public static ObsSnapshot capture(Player player) {
        var loc = player.getLocation();

        // Cardinal direction from yaw
        String facingStr = blockFaceName(player.getFacing());

        // Held item
        ItemStack mainHand = player.getInventory().getItemInMainHand();
        String heldName = mainHand.getType() == Material.AIR
                ? "air"
                : mainHand.getType().getKey().getKey();

        // Inventory (hotbar 0-8 + main 9-35 + offhand 40)
        PlayerInventory pi = player.getInventory();
        Map<String, Integer> invMap = new LinkedHashMap<>();
        for (int slot = 0; slot <= 35; slot++) {
            ItemStack item = pi.getItem(slot);
            if (item != null && item.getType() != Material.AIR) {
                String name = item.getType().getKey().getKey();
                invMap.merge(name, item.getAmount(), Integer::sum);
            }
        }
        // Offhand
        ItemStack offhand = pi.getItemInOffHand();
        if (offhand.getType() != Material.AIR) {
            invMap.merge(offhand.getType().getKey().getKey(), offhand.getAmount(), Integer::sum);
        }

        return new ObsSnapshot(
                roundPos(loc.getX()), roundPos(loc.getY()), roundPos(loc.getZ()),
                facingStr,
                Math.round(player.getHealth() * 10.0) / 10.0,
                player.getFoodLevel(),
                heldName,
                invMap,
                player.getLevel()
        );
    }

    /** Render as a JSON object string (no outer { } wrapper needed — it IS the full object). */
    public String toJson() {
        StringBuilder sb = new StringBuilder();
        sb.append("{");
        sb.append("\"pos\":[")
          .append(posX).append(",").append(posY).append(",").append(posZ)
          .append("]");
        sb.append(",\"facing\":\"").append(facing).append("\"");
        sb.append(",\"health\":").append(health);
        sb.append(",\"hunger\":").append(hunger);
        sb.append(",\"held\":\"").append(jsonEscape(held)).append("\"");
        sb.append(",\"inv\":{");
        boolean first = true;
        for (var entry : inv.entrySet()) {
            if (!first) sb.append(",");
            sb.append("\"").append(jsonEscape(entry.getKey())).append("\":")
              .append(entry.getValue());
            first = false;
        }
        sb.append("}");
        sb.append(",\"xp\":").append(xp);
        sb.append("}");
        return sb.toString();
    }

    // ─── Helpers ──────────────────────────────────────────────────────────────

    private static double roundPos(double v) {
        return Math.round(v * 100.0) / 100.0;
    }

    private static String blockFaceName(BlockFace face) {
        return switch (face) {
            case NORTH -> "North";
            case SOUTH -> "South";
            case EAST  -> "East";
            case WEST  -> "West";
            default    -> face.name().charAt(0) + face.name().substring(1).toLowerCase();
        };
    }

    static String jsonEscape(String s) {
        if (s == null) return "";
        return s.replace("\\", "\\\\")
                .replace("\"", "\\\"")
                .replace("\n", "\\n")
                .replace("\r", "\\r")
                .replace("\t", "\\t");
    }
}
