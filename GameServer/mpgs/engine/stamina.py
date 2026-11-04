"""Player stamina, server side (ArchonHealthAttributeSet.Stamina is server-authoritative and replicated).

Read from the game:
- spend: each Notify_ConsumeStamina "Stamina Cost" in a move's montage at its time (engine.combat_moves) ->
  BP_PlayerCharacter.ConsumeStamina -> ArchonStaminaComponent.TryConsumeStamina.
- sprint: BP_PlayerCharacter."Evaluate Stamina System" drains SPRINT STAMINA DRAIN PER SECOND (3.75) x
  SprintStaminaCostMultiplier while sprinting; it does not regenerate while draining.
- regen: the same tick adds StaminaRegen (9/s in the join) x dt, capped at MaxStamina, when CanRegenStamina;
  StaminaComponent.StaminaRegenDelayAfterUse is 1.4 s.
Assumed: CanRegenStamina = no spend within the delay (native); the weapon power row's regen multiplier is 1; a spend
larger than what is left empties stamina and is reported to the client as out of stamina (ClientHandleOutOfStamina).
Not covered yet: the dodge (BASE DODGE STAMINA COST 20) - the dodge is detected natively (GetIsDodging).
"""
SPRINT_DRAIN = 3.75
REGEN_DELAY = 1.4
SEND_INTERVAL = 0.25
RESYNC_INTERVAL = 2.0     # the server is authoritative: resend even unchanged, so a client-side spend never sticks


class Stamina:
    def __init__(self, value=100.0, maximum=100.0, regen=9.0):
        self.value, self.max, self.regen = float(value), float(maximum), float(regen)
        self.sprinting = False
        self.last_use = None
        self.last_t = None
        self.sent = self.value
        self.sent_t = None

    def spend(self, cost, now):
        """-> True when there was enough; otherwise stamina empties and the caller reports out of stamina."""
        self.tick(now)
        if cost <= 0.0:
            return True
        self.last_use = now
        if self.value >= cost:
            self.value -= cost
            return True
        self.value = 0.0
        return False

    def tick(self, now):
        if self.last_t is not None and now > self.last_t:
            dt = now - self.last_t
            if self.sprinting and self.value > 0.0:
                self.value = max(0.0, self.value - SPRINT_DRAIN * dt)
                self.last_use = now
            elif self.last_use is None or now - self.last_use >= REGEN_DELAY:
                self.value = min(self.max, self.value + self.regen * dt)
        self.last_t = now

    def due_update(self, now):
        """The value to replicate now, or None (changed, and at most every SEND_INTERVAL; the final full value always)."""
        if abs(self.value - self.sent) < 0.01 and (self.sent_t is None or now - self.sent_t < RESYNC_INTERVAL):
            if self.sent_t is None:
                self.sent_t = now
            return None
        if self.sent_t is not None and now - self.sent_t < SEND_INTERVAL and self.value < self.max:
            return None
        self.sent, self.sent_t = self.value, now
        return round(self.value, 2)
