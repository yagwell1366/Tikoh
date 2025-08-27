import json
import logging
import os
from typing import List, Dict, Any

import discord
from discord.ext import commands


# =========================
# Configuration placeholders
# =========================
# Server owner ID placeholder (will be validated against ctx.guild.owner_id at runtime)
OWNER_ID = int(os.getenv("TIKO_HELPER_OWNER_ID", "887330488593842177"))

# JSON file to persist isolation configuration
ISO_PERMS_FILE = os.getenv("TIKO_HELPER_ISO_PERMS_FILE", "isolation_perms.json")

# Staff log channel placeholder (used for staff notifications)
STAFF_LOG_CHANNEL_ID = int(os.getenv("TIKO_HELPER_STAFF_CHANNEL_ID", "1349774308309733397"))


def _default_store() -> Dict[str, Any]:
    # Note: 'isolated_users' persists currently isolated members across restarts
    return {"allowed_ids": [], "roles": [], "channels": [], "isolated_users": []}


def _load_store() -> Dict[str, Any]:
    if not os.path.exists(ISO_PERMS_FILE):
        return _default_store()
    try:
        with open(ISO_PERMS_FILE, "r", encoding="utf-8") as file:
            data = json.load(file)
            # Backwards compatibility: previously a flat list of allowed IDs
            if isinstance(data, list):
                return {"allowed_ids": [int(x) for x in data if str(x).isdigit()], "roles": [], "channels": [], "isolated_users": []}
            if isinstance(data, dict):
                store = _default_store()
                store["allowed_ids"] = [int(x) for x in data.get("allowed_ids", []) if str(x).isdigit()]
                store["roles"] = [int(x) for x in data.get("roles", []) if str(x).isdigit()]
                store["channels"] = [int(x) for x in data.get("channels", []) if str(x).isdigit()]
                store["isolated_users"] = [int(x) for x in data.get("isolated_users", []) if str(x).isdigit()]
                return store
    except Exception as error:  # noqa: BLE001 - log and fallback
        logging.warning("Failed to load isolation store: %s", error)
    return _default_store()


def _save_store(store: Dict[str, Any]) -> None:
    try:
        normalized = {
            "allowed_ids": sorted(set(int(x) for x in store.get("allowed_ids", []))),
            "roles": sorted(set(int(x) for x in store.get("roles", []))),
            "channels": sorted(set(int(x) for x in store.get("channels", []))),
            "isolated_users": sorted(set(int(x) for x in store.get("isolated_users", []))),
        }
        with open(ISO_PERMS_FILE, "w", encoding="utf-8") as file:
            json.dump(normalized, file, indent=2)
    except Exception as error:  # noqa: BLE001 - log and continue
        logging.error("Failed to save isolation store: %s", error)


class IsolationCog(commands.Cog):
    """Cog providing isolated permission management and an isolate command.

    Notes:
        - Commands in this module expect the bot to support the '.' prefix.
        - Only the server owner can modify isolation permissions.
        - The actual isolation behavior is not implemented yet.
    """

    def __init__(self, bot: commands.Bot, owner_id: int | None = None, staff_channel_id: int | None = None) -> None:
        self.bot = bot
        self.owner_id = owner_id or OWNER_ID
        self.staff_channel_id = staff_channel_id or STAFF_LOG_CHANNEL_ID
        store = _load_store()
        self._allowed_ids: List[int] = list(store.get("allowed_ids", []))
        self._roles: List[int] = list(store.get("roles", []))
        self._channels: List[int] = list(store.get("channels", []))
        self._isolated_users: List[int] = list(store.get("isolated_users", []))
        # In-memory cache: {guild_id: {user_id: [role_ids...]}}
        self._isolation_cache: Dict[int, Dict[int, List[int]]] = {}

    # ---------- Utility ----------
    @staticmethod
    def _is_guild_owner(ctx: commands.Context) -> bool:
        return bool(ctx.guild and ctx.author.id == ctx.guild.owner_id)

    def _is_app_owner(self, ctx: commands.Context) -> bool:
        """Check if invoking user matches configured OWNER_ID."""
        try:
            return bool(ctx.author and ctx.author.id == self.owner_id)
        except Exception:
            return False

    def _persist(self) -> None:
        _save_store(
            {
                "allowed_ids": self._allowed_ids,
                "roles": self._roles,
                "channels": self._channels,
                "isolated_users": self._isolated_users,
            }
        )

    # ---------- Permission helpers ----------
    def _has_isolation_permission(self, member: discord.Member) -> bool:
        if member.guild is None:
            return False
        if member.id in self._allowed_ids:
            return True
        member_role_ids = {role.id for role in getattr(member, "roles", [])}
        return any(role_id in self._allowed_ids for role_id in member_role_ids)

    def _get_isolation_role(self, guild: discord.Guild) -> discord.Role | None:
        for rid in self._roles:
            role = guild.get_role(rid)
            if role is not None:
                return role
        return None

    def _cache_member_roles(self, guild_id: int, user_id: int, role_ids: List[int]) -> None:
        guild_cache = self._isolation_cache.setdefault(guild_id, {})
        guild_cache[user_id] = list(role_ids)
        logging.info(f"Cached roles for user {user_id} in guild {guild_id}: {role_ids}")

    def _pop_cached_member_roles(self, guild_id: int, user_id: int) -> List[int] | None:
        guild_cache = self._isolation_cache.get(guild_id)
        if not guild_cache:
            logging.warning(f"No guild cache found for guild {guild_id}")
            return None
        cached_roles = guild_cache.pop(user_id, None)
        if cached_roles:
            logging.info(f"Retrieved cached roles for user {user_id} in guild {guild_id}: {cached_roles}")
        else:
            logging.warning(f"No cached roles found for user {user_id} in guild {guild_id}")
        return cached_roles

    # ---------- Commands ----------
    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member):
        """Auto-ban members who rejoin while flagged as isolated.

        This uses the persistent isolated_users list. If a user is flagged here,
        we attempt to ban them immediately with a clear reason.
        """
        try:
            if member.guild is None:
                return
            if member.id not in getattr(self, "_isolated_users", []):
                return
            # Attempt to ban
            await member.guild.ban(member, reason="Attempted to evade isolation by leaving and rejoining")
            # Staff log
            staff_ch = member.guild.get_channel(self.staff_channel_id) or self.bot.get_channel(self.staff_channel_id)
            if isinstance(staff_ch, (discord.TextChannel, discord.Thread)):
                embed = discord.Embed(title="Auto-ban: Isolation Evasion", color=discord.Color.red())
                embed.add_field(name="User", value=f"{member} ({member.id})", inline=False)
                await staff_ch.send(embed=embed)
        except Exception:
            # Swallow errors to avoid crashing on listener
            pass
    @commands.guild_only()
    @commands.command(name="isoperm", help="Manage isolation permissions: .isoperm add/remove <id>")
    async def isoperm(self, ctx: commands.Context, action: str | None = None, id_value: str | None = None):
        """Manage the set of role/user IDs allowed to interact with isolation features.

        Usage:
            .isoperm add <role_or_user_id>
            .isoperm remove <role_or_user_id>
        """
        if not self._is_app_owner(ctx):
            await ctx.send("Only the configured owner can use this command.")
            return

        if not action or not id_value:
            await ctx.send("Usage: .isoperm add <id> | .isoperm remove <id>")
            return

        action = action.lower().strip()
        if not id_value.isdigit():
            await ctx.send("ID must be a numeric Discord ID.")
            return

        item_id = int(id_value)

        if action == "add":
            if item_id in self._allowed_ids:
                await ctx.send("That ID is already allowed.")
                return
            self._allowed_ids.append(item_id)
            self._persist()
            await ctx.send(f"Added {item_id} to isolation permissions.")
        elif action == "remove":
            if item_id not in self._allowed_ids:
                await ctx.send("That ID is not in the isolation permissions list.")
                return
            self._allowed_ids = [x for x in self._allowed_ids if x != item_id]
            self._persist()
            await ctx.send(f"Removed {item_id} from isolation permissions.")
        else:
            await ctx.send("Unknown action. Use 'add' or 'remove'.")

    @commands.guild_only()
    @commands.command(name="isolate", help="Isolate a user: .isolate <@user|user_id>")
    @commands.guild_only()
    async def isolate(self, ctx: commands.Context, target: discord.Member | None = None):
        """Isolate a member by stripping roles, assigning isolation role, and limiting visibility.

        Steps:
            1) Verify caller has isolation permissions via allowed IDs/roles
            2) Verify role hierarchy (caller > target), and bot can manage target
            3) Cache target's current roles for future restoration
            4) Remove roles and assign isolation role
            5) Ensure isolation channels are visible to the isolation role
        """
        guild = ctx.guild
        if guild is None:
            await ctx.send("This command must be used in a server.")
            return

        author: discord.Member = ctx.author  # type: ignore[assignment]

        # Permission check: allowed list (IDs or roles)
        if not (self._is_guild_owner(ctx) or self._has_isolation_permission(author)):
            await ctx.send("You do not have isolation permissions.")
            return

        if target is None:
            await ctx.send("Please mention a user or provide their ID. Usage: .isolate <@user|user_id>")
            return

        if target == author:
            await ctx.send("You cannot isolate yourself.")
            return

        # Role hierarchy checks
        if author.top_role <= target.top_role and author.id != guild.owner_id:
            await ctx.send("You cannot isolate a member with an equal or higher top role.")
            return

        # Bot capability checks
        me = guild.me
        if me is None:
            await ctx.send("Bot context missing.")
            return
        can_manage_roles = guild.me.guild_permissions.manage_roles and me.top_role > target.top_role

        isolation_role = self._get_isolation_role(guild)
        if isolation_role is None:
            await ctx.send("Isolation role not set. Run .isolation setup first.")
            return

        # Cache existing roles (excluding @everyone)
        current_roles = [r for r in target.roles if not r.is_default()]  # keep order
        self._cache_member_roles(guild.id, target.id, [r.id for r in current_roles])

        # Try to swap roles to isolation role only. If not possible, continue with channel overwrites.
        if can_manage_roles:
            try:
                await target.edit(roles=[isolation_role], reason=f"Isolated by {author} ({author.id})")
            except (discord.Forbidden, discord.HTTPException):
                # Fall back to channel overwrites-only
                can_manage_roles = False

        # Ensure isolation channels allow view/send for the isolation role
        updated = 0
        # Ensure isolation channels allow isolation role (already handled in setup) and member has access
        for cid in self._channels:
            channel = guild.get_channel(cid) or self.bot.get_channel(cid)
            if channel is None:
                continue
            try:
                # Ensure member-specific overwrites do NOT block in isolation channels
                await channel.set_permissions(target, overwrite=None, reason="Ensure isolated member can access isolation channels")
            except (discord.Forbidden, discord.HTTPException):
                pass
        
        # For all other channels, add member-specific deny-all if roles couldn't be fully removed
        if not can_manage_roles:
            deny_member = discord.PermissionOverwrite(
                view_channel=False,
                send_messages=False,
                send_messages_in_threads=False,
                create_public_threads=False,
                create_private_threads=False,
                add_reactions=False,
                connect=False,
                speak=False,
                stream=False,
                use_voice_activation=False,
                send_tts_messages=False,
            )
            for channel in guild.channels:
                if channel.id in self._channels:
                    continue
                try:
                    await channel.set_permissions(target, overwrite=deny_member, reason="Isolation member deny-all (unmanageable roles)")
                    updated += 1
                except (discord.Forbidden, discord.HTTPException):
                    continue

        # Persist isolated user record for restart survival
        if target.id not in self._isolated_users:
            self._isolated_users.append(target.id)
            self._persist()

        # Staff notifications and DMs
        try:
            await target.send(
                "You have been isolated in the server. If you leave and rejoin, you will be automatically banned."
            )
        except Exception:
            pass

        # Notify owner and staff log channel
        owner = guild.get_member(guild.owner_id)
        try:
            if owner:
                await owner.send(f"Isolation: {author} isolated {target} in {guild.name}.")
        except Exception:
            pass
        staff_ch = guild.get_channel(self.staff_channel_id) or self.bot.get_channel(self.staff_channel_id)
        if isinstance(staff_ch, (discord.TextChannel, discord.Thread)):
            embed = discord.Embed(title="User Isolated", color=discord.Color.orange())
            embed.add_field(name="Moderator", value=f"{author} ({author.id})", inline=False)
            embed.add_field(name="Target", value=f"{target} ({target.id})", inline=False)
            embed.add_field(name="Channels updated", value=str(updated), inline=False)
            await staff_ch.send(embed=embed)

        await ctx.send(f"{target.mention} has been isolated. Cached roles saved. Isolation channels updated: {updated}.")

    @commands.command(name="unisolate", help="Restore a user from isolation: .unisolate <@user|user_id>")
    @commands.guild_only()
    async def unisolate(self, ctx: commands.Context, target: discord.Member | None = None):
        """Restore a previously isolated member by re-applying cached roles and removing the isolation role."""
        guild = ctx.guild
        if guild is None:
            await ctx.send("This command must be used in a server.")
            return

        author: discord.Member = ctx.author  # type: ignore[assignment]
        if not (self._is_guild_owner(ctx) or self._has_isolation_permission(author)):
            await ctx.send("You do not have isolation permissions.")
            return

        if target is None:
            await ctx.send("Please mention a user or provide their ID. Usage: .unisolate <@user|user_id>")
            return

        me = guild.me
        if me is None or me.top_role <= target.top_role or not guild.me.guild_permissions.manage_roles:
            await ctx.send("I cannot manage that member. Ensure my role is above the target's roles and I have Manage Roles.")
            return

        isolation_role = self._get_isolation_role(guild)
        if isolation_role is None:
            await ctx.send("Isolation role not set.")
            return

        # Check if user is actually isolated
        if target.id not in self._isolated_users:
            await ctx.send("This user is not currently isolated.")
            return

        cached = self._pop_cached_member_roles(guild.id, target.id)
        roles_restored = False
        
        if cached:
            # Rebuild role objects, skipping any that no longer exist or are above the bot
            roles_to_apply: List[discord.Role] = []
            for rid in cached:
                role = guild.get_role(rid)
                if role is None:
                    continue
                if me.top_role <= role:
                    # Skip roles above the bot
                    continue
                roles_to_apply.append(role)

            if roles_to_apply:
                try:
                    await target.edit(roles=roles_to_apply, reason=f"Unisolated by {author} ({author.id})")
                    roles_restored = True
                except discord.Forbidden:
                    await ctx.send("Failed to restore roles due to missing permissions.")
                    return
                except discord.HTTPException as error:
                    await ctx.send(f"Failed to restore roles: {error}")
                    return
            else:
                await ctx.send("Warning: No valid roles found in cache. Proceeding with basic restoration.")
        else:
            await ctx.send("Warning: No cached roles found. Proceeding with basic restoration.")

        # Fallback: Remove isolation role and clear member-specific overwrites
        if not roles_restored:
            try:
                # Remove isolation role if present
                current_roles = [r for r in target.roles if r.id != isolation_role.id]
                await target.edit(roles=current_roles, reason=f"Unisolated by {author} ({author.id}) - fallback")
            except (discord.Forbidden, discord.HTTPException) as error:
                await ctx.send(f"Warning: Could not remove isolation role: {error}")

        # Remove member-specific deny overwrites set during isolation fallback
        overwrites_cleared = 0
        for channel in guild.channels:
            try:
                await channel.set_permissions(target, overwrite=None, reason="Clear isolation member-specific denies")
                overwrites_cleared += 1
            except (discord.Forbidden, discord.HTTPException):
                continue

        # Remove persistent isolation flag
        if target.id in self._isolated_users:
            self._isolated_users = [x for x in self._isolated_users if x != target.id]
            self._persist()

        # Staff notifications
        staff_ch = guild.get_channel(self.staff_channel_id) or self.bot.get_channel(self.staff_channel_id)
        if isinstance(staff_ch, (discord.TextChannel, discord.Thread)):
            embed = discord.Embed(title="User Unisolated", color=discord.Color.green())
            embed.add_field(name="Moderator", value=f"{author} ({author.id})", inline=False)
            embed.add_field(name="Target", value=f"{target} ({target.id})", inline=False)
            embed.add_field(name="Method", value="Cached roles restored" if roles_restored else "Fallback restoration", inline=False)
            embed.add_field(name="Overwrites cleared", value=str(overwrites_cleared), inline=False)
            await staff_ch.send(embed=embed)

        await ctx.send(f"Restored {target.mention} from isolation. {'Roles restored from cache.' if roles_restored else 'Basic restoration completed.'} Overwrites cleared: {overwrites_cleared}")

    # ---------- New: Isolation configuration (roles/channels) ----------
    @commands.guild_only()
    @commands.group(name="isolation", invoke_without_command=True)
    async def isolation_group(self, ctx: commands.Context):
        if not self._is_app_owner(ctx):
            await ctx.send("Only the configured owner can use this command.")
            return
        await ctx.send("Usage: .isolation role add/remove <@role|role_id> | .isolation channel add/remove <#channel|channel_id> | .isolation show")

    @isolation_group.command(name="show")
    async def isolation_show(self, ctx: commands.Context):
        """Show current isolation configuration (allowed IDs, roles, channels)."""
        if not self._is_app_owner(ctx):
            await ctx.send("Only the configured owner can use this command.")
            return

        guild = ctx.guild
        allowed_display: List[str] = []
        if guild is not None:
            for item_id in self._allowed_ids:
                member = guild.get_member(item_id)
                role = guild.get_role(item_id)
                if member is not None:
                    allowed_display.append(f"User: {member.mention} ({member.id})")
                elif role is not None:
                    allowed_display.append(f"Role: {role.name} ({role.id})")
                else:
                    allowed_display.append(f"ID: {item_id}")
        else:
            allowed_display = [str(x) for x in self._allowed_ids]

        role_display: List[str] = []
        if guild is not None:
            for rid in self._roles:
                role = guild.get_role(rid)
                role_display.append(f"{role.name} ({role.id})" if role else f"ID: {rid}")
        else:
            role_display = [str(x) for x in self._roles]

        channel_display: List[str] = []
        if guild is not None:
            for cid in self._channels:
                channel = guild.get_channel(cid) or self.bot.get_channel(cid)
                channel_display.append(f"#{channel.name} ({cid})" if channel else f"ID: {cid}")
        else:
            channel_display = [str(x) for x in self._channels]

        description_parts = []
        description_parts.append("Allowed IDs:\n" + ("\n".join(allowed_display) if allowed_display else "<none>"))
        description_parts.append("Roles:\n" + ("\n".join(role_display) if role_display else "<none>"))
        description_parts.append("Channels:\n" + ("\n".join(channel_display) if channel_display else "<none>"))
        description_parts.append("Isolated Users:\n" + ("\n".join([str(x) for x in self._isolated_users]) if self._isolated_users else "<none>"))

        # Use an embed for readability if possible
        embed = discord.Embed(title="Isolation Configuration", description="\n\n".join(description_parts), color=discord.Color.blurple())
        try:
            await ctx.send(embed=embed)
        except Exception:
            await ctx.send("\n\n".join(description_parts))

    @isolation_group.group(name="role", invoke_without_command=True)
    async def isolation_role(self, ctx: commands.Context):
        if not self._is_app_owner(ctx):
            await ctx.send("Only the configured owner can use this command.")
            return
        await ctx.send("Usage: .isolation role add/remove <@role|role_id>")

    @isolation_role.command(name="add")
    async def isolation_role_add(self, ctx: commands.Context, role: discord.Role | None = None):
        if not self._is_app_owner(ctx):
            await ctx.send("Only the configured owner can use this command.")
            return
        if role is None:
            await ctx.send("Please mention a role or provide its ID.")
            return
        if role.id in self._roles:
            await ctx.send("That role is already configured for isolation.")
            return
        self._roles.append(role.id)
        self._persist()
        await ctx.send(f"Added role {role.name} ({role.id}) to isolation configuration.")

    @isolation_role.command(name="remove")
    async def isolation_role_remove(self, ctx: commands.Context, role: discord.Role | None = None):
        if not self._is_app_owner(ctx):
            await ctx.send("Only the configured owner can use this command.")
            return
        if role is None:
            await ctx.send("Please mention a role or provide its ID.")
            return
        if role.id not in self._roles:
            await ctx.send("That role is not configured for isolation.")
            return
        self._roles = [r for r in self._roles if r != role.id]
        self._persist()
        await ctx.send(f"Removed role {role.name} ({role.id}) from isolation configuration.")

    @isolation_group.group(name="channel", invoke_without_command=True)
    async def isolation_channel(self, ctx: commands.Context):
        if not self._is_app_owner(ctx):
            await ctx.send("Only the configured owner can use this command.")
            return
        await ctx.send("Usage: .isolation channel add/remove <#channel|channel_id>")

    @isolation_channel.command(name="add")
    async def isolation_channel_add(self, ctx: commands.Context, channel: discord.TextChannel | None = None):
        if not self._is_app_owner(ctx):
            await ctx.send("Only the configured owner can use this command.")
            return
        if channel is None:
            await ctx.send("Please mention a channel or provide its ID.")
            return
        if channel.id in self._channels:
            await ctx.send("That channel is already configured for isolation.")
            return
        self._channels.append(channel.id)
        self._persist()
        await ctx.send(f"Added channel {channel.mention} ({channel.id}) to isolation configuration.")

    @isolation_channel.command(name="remove")
    async def isolation_channel_remove(self, ctx: commands.Context, channel: discord.TextChannel | None = None):
        if not self._is_app_owner(ctx):
            await ctx.send("Only the configured owner can use this command.")
            return
        if channel is None:
            await ctx.send("Please mention a channel or provide its ID.")
            return
        if channel.id not in self._channels:
            await ctx.send("That channel is not configured for isolation.")
            return
        self._channels = [c for c in self._channels if c != channel.id]
        self._persist()
        await ctx.send(f"Removed channel {channel.mention} ({channel.id}) from isolation configuration.")

    @isolation_group.command(name="setup")
    async def isolation_setup(self, ctx: commands.Context, role_name: str = "Isolation"):
        """Create the isolation role (if missing), persist it, and deny all permissions in all channels.

        - Creates a role named `role_name` if it does not exist
        - Saves the role ID to the isolation roles list
        - Applies deny-all overwrites for that role across all guild channels
        """
        if not self._is_app_owner(ctx):
            await ctx.send("Only the configured owner can use this command.")
            return

        guild = ctx.guild
        if guild is None:
            await ctx.send("This command must be used in a server.")
            return

        # Find or create the role
        role = discord.utils.get(guild.roles, name=role_name)
        if role is None:
            try:
                role = await guild.create_role(name=role_name, reason="Create isolation role")
                await ctx.send(f"Created isolation role: {role.name} ({role.id}).")
            except discord.Forbidden:
                await ctx.send("I lack permissions to create roles.")
                return
            except discord.HTTPException as error:
                await ctx.send(f"Failed to create role: {error}")
                return
        else:
            await ctx.send(f"Using existing isolation role: {role.name} ({role.id}).")

        # Persist role ID if not already stored
        if role.id not in self._roles:
            self._roles.append(role.id)
            self._persist()

        # Define deny-all permissions for text/voice/categories
        deny_overwrites = discord.PermissionOverwrite(
            view_channel=False,
            send_messages=False,
            send_messages_in_threads=False,
            create_public_threads=False,
            create_private_threads=False,
            add_reactions=False,
            connect=False,
            speak=False,
            stream=False,
            use_voice_activation=False,
            send_tts_messages=False,
        )

        # Apply/merge deny-all overwrites for the isolation role across all channels
        updated_channels = 0
        errors = 0
        for channel in guild.channels:
            try:
                current = channel.overwrites_for(role)
                needs_update = False
                for attr, value in deny_overwrites:
                    if getattr(current, attr) != value:
                        setattr(current, attr, value)
                        needs_update = True
                if needs_update:
                    await channel.set_permissions(role, overwrite=current, reason="Isolation setup deny-all")
                    updated_channels += 1
            except discord.Forbidden:
                errors += 1
            except discord.HTTPException:
                errors += 1

        await ctx.send(
            f"Isolation setup complete. Role: {role.mention}. Updated {updated_channels} channel(s)."
            + (f" {errors} channel(s) failed due to permissions." if errors else "")
        )

        # Create or configure a private isolation channel visible only to the isolation role
        try:
            # Prepare overwrites: block @everyone, allow isolation role
            overwrites: Dict[discord.abc.Snowflake, discord.PermissionOverwrite] = {
                guild.default_role: discord.PermissionOverwrite(view_channel=False),
                role: discord.PermissionOverwrite(
                    view_channel=True,
                    send_messages=True,
                    add_reactions=True,
                    read_message_history=True,
                ),
            }

            # Look for an existing channel named "isolation" or "quarantine"
            target_channel = discord.utils.get(guild.text_channels, name="isolation")
            if target_channel is None:
                target_channel = discord.utils.get(guild.text_channels, name="quarantine")

            if target_channel is None:
                target_channel = await guild.create_text_channel(
                    name="isolation",
                    overwrites=overwrites,
                    reason="Create private isolation channel visible only to isolation role",
                )
            else:
                # Ensure overwrites are correct
                await target_channel.edit(overwrites=overwrites, reason="Ensure isolation channel privacy")

            if target_channel and target_channel.id not in self._channels:
                self._channels.append(target_channel.id)
                self._persist()

            await ctx.send(f"Private isolation channel ready: {target_channel.mention}")
        except discord.Forbidden:
            await ctx.send("I lack permissions to create or configure the isolation channel.")
        except discord.HTTPException as error:
            await ctx.send(f"Failed to create/configure isolation channel: {error}")

    # ---------- Cleanup Commands ----------
    @isolation_group.command(name="cleanup")
    async def isolation_cleanup(self, ctx: commands.Context):
        """Remove entries from isolated-users list for members already banned.

        Note: does NOT remove entries for users who simply left (they may rejoin).
        Owner-only.
        """
        if not self._is_app_owner(ctx):
            await ctx.send("Only the configured owner can use this command.")
            return
        guild = ctx.guild
        if guild is None:
            await ctx.send("This command must be used in a server.")
            return
        # Build a set of banned user IDs
        banned_ids: set[int] = set()
        try:
            bans = await guild.bans()
            for entry in bans:
                if entry.user:
                    banned_ids.add(entry.user.id)
        except Exception:
            pass

        before = set(self._isolated_users)
        after = [uid for uid in self._isolated_users if uid not in banned_ids]
        removed = len(before) - len(set(after))
        self._isolated_users = after
        self._persist()
        await ctx.send(f"Cleanup complete. Removed {removed} banned user(s) from isolated list. Remaining: {len(self._isolated_users)}.")

    @isolation_group.command(name="clearcache")
    async def isolation_clearcache(self, ctx: commands.Context):
        """Clear all isolation cache entries (owner-only)."""
        if not self._is_guild_owner(ctx):
            await ctx.send("Only the server owner can use this command.")
            return
        self._isolation_cache.clear()
        await ctx.send("Isolation in-memory cache cleared.")

        


# Extension-style setup is optional; we primarily add the cog directly from Main.py.
def setup(bot: commands.Bot):  # pragma: no cover - compatibility hook
    bot.add_cog(IsolationCog(bot))


