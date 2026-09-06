import random
import logging
from datetime import datetime, timedelta
from collections import Counter
from telegram import InlineKeyboardButton, InlineKeyboardMarkup
from telegram.error import TelegramError
from database import (
    SessionLocal, Game, Player, NightAction, Vote, Evidence, Event,
    PersonalGoal, GameHistory,
)
from config import MIN_PLAYERS, MAX_PLAYERS, NIGHT_SECONDS, DISCUSSION_SECONDS, VOTE_SECONDS
from data_roles import ROLES, GOALS
from data_events import EVENTS
from data_evidence import EVIDENCE

log = logging.getLogger("last_night.engine")

ACTIVE_PHASES = ("night", "discussion", "vote")
SELF_ALLOWED_ACTIONS = {"protect", "guard"}
ZOMBIE_ACTION_LABELS = {"infect": "🧟", "follow": "👁️", "disrupt": "🤫", "fake": "🎭"}


def session():
    return SessionLocal()


def current_game(db, chat_id):
    """The active game for a chat is always the most recently created row."""
    return db.query(Game).filter_by(chat_id=chat_id).order_by(Game.id.desc()).first()


def current_player(db, user_id):
    """Most recent alive player row for this Telegram user (handles a user
    being in more than one game's history, though acting in two *active*
    games at once is not supported)."""
    return (
        db.query(Player)
        .filter_by(user_id=user_id, alive=True)
        .order_by(Player.id.desc())
        .first()
    )


TELEGRAM_MAX_LEN = 4000  # stay safely under Telegram's 4096-char hard limit


async def safe_send(bot, chat_id, text, reply_markup=None):
    """Sends text to a chat, splitting on paragraph boundaries if it would
    exceed Telegram's message length limit (this matters a lot here: the
    morning recap and the final cinematic reveal can both get long with
    close to MAX_PLAYERS players)."""
    if len(text) <= TELEGRAM_MAX_LEN:
        try:
            await bot.send_message(chat_id, text, reply_markup=reply_markup)
            return True
        except TelegramError as e:
            log.warning("Could not message %s: %s", chat_id, e)
            return False

    chunks, current = [], ""
    for line in text.split("\n"):
        if len(current) + len(line) + 1 > TELEGRAM_MAX_LEN:
            chunks.append(current)
            current = line
        else:
            current = f"{current}\n{line}" if current else line
    if current:
        chunks.append(current)

    ok = True
    for i, chunk in enumerate(chunks):
        markup = reply_markup if i == len(chunks) - 1 else None
        try:
            await bot.send_message(chat_id, chunk, reply_markup=markup)
        except TelegramError as e:
            log.warning("Could not message %s: %s", chat_id, e)
            ok = False
    return ok


# --------------------------------------------------------------------------
# Lobby
# --------------------------------------------------------------------------

def lobby_text(players):
    lines = [f"🧟 ՎԵՐՋԻՆ ԳԻՇԵՐԸ\n\n👥 Խաղացողներ — {len(players)}/{MAX_PLAYERS}"]
    for i, p in enumerate(players, 1):
        lines.append(f"{i}. {p.name}")
    lines.append(f"\nԽաղն սկսելու համար պետք է առնվազն {MIN_PLAYERS} խաղացող։")
    return "\n".join(lines)


def lobby_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("➕ Միանալ", callback_data="lobby:join")],
        [InlineKeyboardButton("🚪 Լքել", callback_data="lobby:leave")],
        [InlineKeyboardButton("▶️ Սկսել", callback_data="lobby:start")],
    ])


async def create_game(chat_id, user):
    db = session()
    try:
        old = current_game(db, chat_id)
        if old and old.phase != "finished":
            players = db.query(Player).filter_by(game_id=old.id).all()
            return "ℹ️ Այս խմբում արդեն կա ակտիվ խաղ կամ lobby։\n\n" + lobby_text(players), lobby_keyboard()
        g = Game(chat_id=chat_id, creator_id=user.id)
        db.add(g)
        db.commit()
        p = Player(game_id=g.id, user_id=user.id, name=user.full_name)
        db.add(p)
        db.commit()
        return (
            "🧟 ՎԵՐՋԻՆ ԳԻՇԵՐԸ\n\n"
            "☣️ Քաղաքը վարակված է։\n"
            "👥 Խաղացողները պետք է գոյատևեն մինչև փրկարարների ժամանումը։\n"
            "⚠️ Բայց խմբի մեջ կա գաղտնի zombie։\n\n" + lobby_text([p]),
            lobby_keyboard(),
        )
    finally:
        db.close()


async def join_game(chat_id, user):
    db = session()
    try:
        g = current_game(db, chat_id)
        if not g or g.phase != "lobby":
            return "❌ Ակտիվ lobby չկա։ Սկսիր /zombie-ով։", None
        players = db.query(Player).filter_by(game_id=g.id).all()
        if any(p.user_id == user.id for p in players):
            return "ℹ️ Դու արդեն միացած ես։\n\n" + lobby_text(players), lobby_keyboard()
        if len(players) >= MAX_PLAYERS:
            return "❌ Lobby-ն լիքն է։\n\n" + lobby_text(players), lobby_keyboard()
        db.add(Player(game_id=g.id, user_id=user.id, name=user.full_name))
        db.commit()
        players = db.query(Player).filter_by(game_id=g.id).all()
        return lobby_text(players), lobby_keyboard()
    finally:
        db.close()


async def leave_game(chat_id, user_id):
    db = session()
    try:
        g = current_game(db, chat_id)
        if not g or g.phase != "lobby":
            return "❌ Lobby չկա (խաղը կամ չի սկսվել, կամ արդեն ընթացքի մեջ է)։", None
        p = db.query(Player).filter_by(game_id=g.id, user_id=user_id).first()
        if p:
            db.delete(p)
            db.commit()
        players = db.query(Player).filter_by(game_id=g.id).all()
        return lobby_text(players), lobby_keyboard()
    finally:
        db.close()


def assign_roles(players):
    """Scale role distribution to player count. Always at least one zombie,
    two zombies from 16 players up."""
    n = len(players)
    zombie_count = max(1, n // 8)
    core_roles = ["doctor", "investigator", "guard", "radio", "scientist", "journalist", "double_agent"]
    roles = ["zombie"] * zombie_count
    remaining_slots = n - zombie_count
    roles += core_roles[:max(0, min(len(core_roles), remaining_slots))]
    while len(roles) < n:
        roles.append("survivor")
    roles = roles[:n]
    if "zombie" not in roles:
        roles[0] = "zombie"
    random.shuffle(roles)
    return roles


async def start_game(bot, chat_id, user):
    db = session()
    try:
        g = current_game(db, chat_id)
        if not g or g.phase != "lobby":
            return "❌ Սկսելու lobby չկա։", None
        if g.creator_id != user.id:
            return "⛔ Միայն lobby-ի creator-ը կարող է սկսել խաղը։", lobby_keyboard()
        players = db.query(Player).filter_by(game_id=g.id).all()
        if not (MIN_PLAYERS <= len(players) <= MAX_PLAYERS):
            return f"❌ Պետք է լինի {MIN_PLAYERS}–{MAX_PLAYERS} խաղացող (հիմա՝ {len(players)})։", lobby_keyboard()

        roles = assign_roles(players)
        for p, r in zip(players, roles):
            p.role = r
            p.goal = random.choice(GOALS[r])
            db.add(PersonalGoal(game_id=g.id, player_id=p.id, text=p.goal))

        g.phase = "night"
        g.round_no = 1
        g.started_at = datetime.utcnow()
        g.phase_until = datetime.utcnow() + timedelta(seconds=NIGHT_SECONDS)
        db.add(GameHistory(game_id=g.id, round_no=1, phase="night", description="Խաղը սկսվեց։"))
        db.commit()

        # Private, secret role + goal delivery — required by design.
        for p in players:
            info = ROLES[p.role]
            await safe_send(
                bot, p.user_id,
                f"🎭 Քո գաղտնի role-ը՝ {info['name']}\n\n{info['desc']}\n\n"
                f"🎯 Անձնական նպատակդ՝ {p.goal}\n\n"
                "⚠️ Երբեք մի բացահայտիր սա բացահայտ խմբում, եթե ինքդ չես ուզում խաղալ bluff։",
            )

        await send_night_prompts(bot, db, g, players)

        return (
            "🧟 Խաղը սկսվեց՝ role-երը բաժանվեցին private message-ով։\n\n"
            "🌙 ԳԻՇԵՐ 1\n\n"
            "🌙 Գիշերը իջավ քաղաքի վրա։\n"
            "🔒 Բոլորը փակվեք անվտանգ վայրերում։\n"
            "Գիշերային գործողությունները սկսվել են։\n"
            f"⏱ {NIGHT_SECONDS} վայրկյան։"
        ), None
    finally:
        db.close()


async def get_status(chat_id):
    db = session()
    try:
        g = current_game(db, chat_id)
        if not g:
            return "❌ Ակտիվ խաղ չկա։ Սկսիր /zombie-ով։"
        ps = db.query(Player).filter_by(game_id=g.id).all()
        alive = sum(p.alive for p in ps)
        phase_names = {
            "lobby": "Lobby", "night": "🌙 Գիշեր", "discussion": "💬 Քննարկում",
            "vote": "🗳️ Քվեարկություն", "finished": "✅ Ավարտված",
        }
        return (
            f"🧟 ՎԵՐՋԻՆ ԳԻՇԵՐԸ\n"
            f"Փուլ՝ {phase_names.get(g.phase, g.phase)}\n"
            f"Round՝ {g.round_no}\n"
            f"👥 Ողջ՝ {alive}/{len(ps)}"
        )
    finally:
        db.close()


# --------------------------------------------------------------------------
# Night action keyboards + delivery
# --------------------------------------------------------------------------

def _solo_action_keyboard(action, targets):
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(p.name, callback_data=f"act:{action}:{p.id}")] for p in targets
    ])


def _vote_keyboard(targets):
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(p.name, callback_data=f"vote:{p.id}")] for p in targets
    ])


def _zombie_keyboard(targets):
    rows = []
    for p in targets:
        rows.append([
            InlineKeyboardButton(f"{ZOMBIE_ACTION_LABELS[a]} {p.name}", callback_data=f"act:{a}:{p.id}")
            for a in ("infect", "follow", "disrupt", "fake")
        ])
    return InlineKeyboardMarkup(rows)


async def send_night_prompts(bot, db, g, players):
    alive = [p for p in players if p.alive]
    for p in alive:
        others = [x for x in alive if x.id != p.id]
        if p.role == "zombie":
            await safe_send(
                bot, p.user_id,
                f"🧟 ԳԻՇԵՐԱՅԻՆ ԳՈՐԾՈՂՈՒԹՅՈՒՆ — Round {g.round_no}\n\n"
                "Ընտրիր խաղացող և գործողություն.\n"
                "🧟 Վարակել  👁️ Հետևել  🤫 Խանգարել  🎭 Կեղծ հետք\n"
                "(👁️/🤫/🎭 ունեն cooldown)",
                _zombie_keyboard(others or alive),
            )
        elif p.role == "doctor":
            await safe_send(bot, p.user_id, "🩺 Ո՞ւմ ես պաշտպանելու այս գիշեր (կարող ես նաև ինքդ քեզ)։",
                             _solo_action_keyboard("protect", alive))
        elif p.role == "guard":
            await safe_send(bot, p.user_id, "🛡️ Ո՞ւմ ես պահապանելու այս գիշեր (կարող ես նաև ինքդ քեզ)։",
                             _solo_action_keyboard("guard", alive))
        elif p.role == "investigator":
            await safe_send(bot, p.user_id, "🔎 Ո՞ւմ ես ստուգելու։", _solo_action_keyboard("investigate", others))
        elif p.role == "scientist":
            await safe_send(bot, p.user_id, "🔬 Ո՞ւմ վարակի մակարդակը ես վերլուծելու։",
                             _solo_action_keyboard("analyze", others))
        else:
            await safe_send(
                bot, p.user_id,
                f"🌙 Գիշեր {g.round_no}. Այս գիշեր հատուկ գործողություն չունես, բայց մնա զգոն "
                "և հիշիր քո անձնական նպատակը (/goal)։",
            )


# --------------------------------------------------------------------------
# Private action + vote submission
# --------------------------------------------------------------------------

async def handle_private_action(user_id, action, message):
    db = session()
    try:
        p = current_player(db, user_id)
        if not p:
            return "❌ Դու ակտիվ խաղացող չես ոչ մի ընթացիկ խաղում։"
        g = db.query(Game).filter_by(id=p.game_id).first()

        if action == "/role":
            info = ROLES[p.role]
            return f"🎭 Քո գաղտնի role-ը՝ {info['name']}\n\n{info['desc']}"
        if action == "/goal":
            return f"🎯 Քո personal goal-ը՝\n{p.goal}"
        if action == "/inventory":
            items = db.query(Evidence).filter_by(owner_id=p.id, game_id=g.id).all()
            if not items:
                return "🎒 Inventory\nԴեռ evidence չունես։"
            return "🎒 Inventory\n" + "\n".join(f"• {x.kind}: {x.description}" for x in items)

        if action.startswith("act:"):
            _, typ, target_raw = action.split(":")
            target_id = int(target_raw)
            if g.phase != "night":
                return "⏳ Գիշերային գործողության ժամանակը կամ ավարտվել է, կամ դեռ չի սկսվել։"
            allowed = {
                "zombie": {"infect", "follow", "disrupt", "fake"},
                "doctor": {"protect"},
                "investigator": {"investigate"},
                "guard": {"guard"},
                "scientist": {"analyze"},
            }
            if typ not in allowed.get(p.role, set()):
                return "❌ Քո role-ը այս գործողությունը չունի։"
            if target_id == p.id and typ not in SELF_ALLOWED_ACTIONS:
                return "❌ Չես կարող քեզ ընտրել այս գործողության համար։"
            target = db.query(Player).filter_by(id=target_id, game_id=g.id, alive=True).first()
            if not target:
                return "❌ Անվավեր կամ այլևս ողջ չգտնվող թիրախ։"
            if typ == "infect" and target.role == "zombie":
                return "❌ Չես կարող վարակել մեկ ուրիշ zombie-ի։"
            if typ in ("follow", "disrupt", "fake") and p.special_cooldown_until > g.round_no:
                return f"⏳ Այս ունակությունը cooldown-ի մեջ է ևս {p.special_cooldown_until - g.round_no} round։"
            if db.query(NightAction).filter_by(game_id=g.id, round_no=g.round_no, actor_id=p.id).first():
                return "❌ Այս գիշեր արդեն գործողություն կատարել ես։"
            db.add(NightAction(game_id=g.id, round_no=g.round_no, actor_id=p.id, target_id=target_id, action=typ))
            if typ in ("follow", "disrupt", "fake"):
                p.special_cooldown_until = g.round_no + 2
            db.commit()
            return "✅ Գործողությունը գաղտնի ընդունվեց։ Արդյունքը կիմանաս առավոտյան։"

        if action.startswith("vote:"):
            target_id = int(action.split(":")[1])
            if g.phase != "vote":
                return "⏳ Քվեարկություն այս պահին չկա։"
            if target_id == p.id:
                return "❌ Ինքդ քեզ քվեարկել չես կարող։"
            if db.query(Vote).filter_by(game_id=g.id, round_no=g.round_no, voter_id=p.id).first():
                return "❌ Դու արդեն քվեարկել ես այս round-ում։"
            if not db.query(Player).filter_by(id=target_id, game_id=g.id, alive=True).first():
                return "❌ Թիրախը հասանելի չէ։"
            db.add(Vote(game_id=g.id, round_no=g.round_no, voter_id=p.id, target_id=target_id))
            db.commit()
            return "🗳️ Քո ձայնը գաղտնի ընդունվեց։"

        return "❓ Անհայտ գործողություն։"
    finally:
        db.close()


async def publish_fact(chat_id, user_id):
    """Journalist-only, used as a public /publish command inside the group."""
    db = session()
    try:
        g = current_game(db, chat_id)
        if not g or g.phase == "lobby" or g.phase == "finished":
            return "❌ Ակտիվ խաղ չկա։"
        p = db.query(Player).filter_by(game_id=g.id, user_id=user_id, alive=True).first()
        if not p or p.role != "journalist":
            return "❌ Այս հրամանը միայն լրագրողինն է։"
        if g.phase != "discussion":
            return "❌ /publish-ը հասանելի է միայն քննարկման փուլում։"
        if p.has_published:
            return "❌ Դու արդեն օգտագործել ես քո մեկանգամյա հրապարակումը այս խաղում։"
        candidate = (
            db.query(Evidence)
            .filter_by(game_id=g.id, public=False)
            .order_by(Evidence.id.desc())
            .first()
        )
        if not candidate:
            return "❌ Հրապարակելու ապացույց դեռ չկա։"
        candidate.public = True
        p.has_published = True
        p.score += 15
        db.add(GameHistory(
            game_id=g.id, round_no=g.round_no, phase="discussion", actor=p.name,
            description=f"📰 Լրագրողը հրապարակեց մի փաստ՝ {candidate.kind}՝ {candidate.description}",
        ))
        db.commit()
        return f"📰 ՀՐԱՊԱՐԱԿՈՒՄ ({p.name})\n\n{candidate.kind}: {candidate.description}"
    finally:
        db.close()


# --------------------------------------------------------------------------
# Phase engine
# --------------------------------------------------------------------------

async def advance_if_ready(bot):
    db = session()
    try:
        games = db.query(Game).filter(Game.phase.in_(ACTIVE_PHASES)).all()
        for g in games:
            now = datetime.utcnow()
            players = db.query(Player).filter_by(game_id=g.id).all()

            if g.phase == "night" and g.phase_until and now >= g.phase_until:
                morning_text = await resolve_night(bot, db, g, players)
                g.phase = "discussion"
                g.phase_until = now + timedelta(seconds=DISCUSSION_SECONDS)
                db.commit()
                await safe_send(bot, g.chat_id, morning_text)
                await safe_send(
                    bot, g.chat_id,
                    f"💬 ՔՆՆԱՐԿՄԱՆ ՓՈՒԼ — {DISCUSSION_SECONDS // 60}:{DISCUSSION_SECONDS % 60:02d}\n\n"
                    "Ո՞վ է կասկածելի։ Ո՞վ ինչ է տեսել։ Ո՞ւմ պատմությունն է հակասում մյուսներին։\n"
                    "(Լրագրողը կարող է այս փուլում օգտագործել /publish)",
                )

            elif g.phase == "discussion" and g.phase_until and now >= g.phase_until:
                g.phase = "vote"
                g.phase_until = now + timedelta(seconds=VOTE_SECONDS)
                db.commit()
                alive = [p for p in players if p.alive]
                await safe_send(bot, g.chat_id, "🗳️ ՔՎԵԱՐԿՈՒԹՅՈՒՆ\n\nԳաղտնի քվեարկությունը սկսվեց։")
                for p in alive:
                    await safe_send(bot, p.user_id, "🗳️ Ո՞ւմ եք ամենաշատը կասկածում։",
                                     _vote_keyboard([x for x in alive if x.id != p.id]))

            elif g.phase == "vote" and g.phase_until and now >= g.phase_until:
                result_text = resolve_vote(db, g, players)
                g.round_no += 1
                db.commit()
                outcome = check_win(g, players)
                if outcome:
                    finish_game(db, g, players, outcome)
                    db.commit()
                    await safe_send(bot, g.chat_id, result_text)
                    await safe_send(bot, g.chat_id, cinematic(db, g, players, outcome))
                else:
                    g.phase = "night"
                    g.phase_until = now + timedelta(seconds=NIGHT_SECONDS)
                    db.commit()
                    await send_night_prompts(bot, db, g, players)
                    await safe_send(bot, g.chat_id, result_text + f"\n\n🌙 Գիշեր {g.round_no} սկսվեց։ ⏱ {NIGHT_SECONDS}վ")
    finally:
        db.close()


def _infection_label(stage):
    return {"healthy": "🟢 Healthy", "exposed": "🟡 Exposed", "infected": "🟠 Infected", "critical": "🔴 Critical"}[stage]


def _advance_infection(p, steps=1):
    order = ["healthy", "exposed", "infected", "critical"]
    idx = min(order.index(p.infection) + steps, len(order) - 1)
    p.infection = order[idx]
    if p.infection == "critical":
        p.alive = False


def _heal_infection(p, steps=1):
    order = ["healthy", "exposed", "infected", "critical"]
    idx = max(order.index(p.infection) - steps, 0)
    p.infection = order[idx]


async def resolve_night(bot, db, g, players):
    actions = db.query(NightAction).filter_by(game_id=g.id, round_no=g.round_no).all()
    infects = [a for a in actions if a.action == "infect"]
    follows = [a for a in actions if a.action == "follow"]
    disrupts = [a for a in actions if a.action == "disrupt"]
    fakes = [a for a in actions if a.action == "fake"]
    investigates = [a for a in actions if a.action == "investigate"]
    analyzes = [a for a in actions if a.action == "analyze"]
    protects = {a.target_id for a in actions if a.action == "protect"}
    guards = {a.target_id for a in actions if a.action == "guard"}
    disrupted_targets = {a.target_id for a in disrupts}

    by_id = {p.id: p for p in players}
    alive = [p for p in players if p.alive]

    # Pick the event of the round.
    title, desc, effect = random.choice(EVENTS)
    db.add(Event(game_id=g.id, round_no=g.round_no, title=title, description=desc, effect=effect))

    dbl_damage = effect == "infection_up"
    vague_only = effect == "investigation_down"
    precise_investigation = effect == "investigation_up"
    doctor_heals = effect == "doctor_bonus"
    scientist_precise = effect == "scientist_bonus"
    guard_blocks_follow = effect == "guard_bonus"
    radio_boost = effect in ("radio_clue", "rescue_clue", "rescue_up")
    radio_down = effect == "radio_down"
    bonus_evidence = effect in ("evidence", "key_clue", "event_clue")
    misleading_bonus = effect == "misleading_evidence"
    morale_delta = 2 if effect == "morale_up" else (-2 if effect == "morale_down" else 0)

    lines = [f"☀️ ԱՌԱՎՈՏ — Round {g.round_no}", "", f"{title}", desc, ""]

    # --- Zombie: infect ---
    for a in infects:
        target = by_id.get(a.target_id)
        if not target or not target.alive:
            continue
        if a.target_id in protects or a.target_id in guards:
            if a.target_id in protects and doctor_heals and target.infection != "healthy":
                _heal_infection(target, 1)
                lines.append(f"🩺 Հարձակումը {target.name}-ի վրա ձախողվեց, և բժիշկը մասամբ բուժեց նրան։")
            else:
                lines.append(f"🛡️ Հարձակումը {target.name}-ի վրա կանխվեց այս գիշեր։")
            db.add(GameHistory(game_id=g.id, round_no=g.round_no, phase="morning", target=target.name,
                                description="Հարձակումը կանխվեց։"))
        else:
            _advance_infection(target, 2 if dbl_damage else 1)
            if not target.alive:
                lines.append(f"💀 Առավոտյան հայտնաբերվեց, որ {target.name}-ը անհետացել է։")
            else:
                lines.append(f"⚠️ Գիշերը ինչ-որ մեկը հարձակվել է {target.name}-ի վրա։ Վիճակը՝ {_infection_label(target.infection)}։")
            db.add(GameHistory(game_id=g.id, round_no=g.round_no, phase="morning", target=target.name,
                                description=f"Ենթարկվեց հարձակման, վիճակ՝ {target.infection}։"))

    # --- Zombie: follow (private info to zombie) ---
    for a in follows:
        target = by_id.get(a.target_id)
        actor = by_id.get(a.actor_id)
        if not target or not actor:
            continue
        if a.target_id in guards and guard_blocks_follow:
            await safe_send(bot, actor.user_id, f"👁️ Փորձեցիր հետևել {target.name}-ին, բայց պահակը խափանեց փորձը։")
            continue
        info = ROLES.get(target.role, {}).get("name", "անհայտ")
        await safe_send(bot, actor.user_id, f"👁️ Հետևման արդյունք՝ {target.name}-ի role-ը նման է՝ {info}")

    # --- Zombie: disrupt is applied when resolving investigate (below) ---

    # --- Zombie: fake evidence ---
    for a in fakes:
        others = [p for p in alive if p.id != a.actor_id]
        if not others:
            continue
        suspect = random.choice(others)
        finder = random.choice(alive)
        ev_kind = random.choice(EVIDENCE)["kind"]
        e = Evidence(
            game_id=g.id, round_no=g.round_no, owner_id=finder.id, kind=ev_kind,
            description=f"Հետքը կասկածելիորեն կապված է {suspect.name}-ի հետ։", misleading=True,
        )
        db.add(e)
        lines.append(f"🕵️ {finder.name}-ը գտավ մի ապացույց, որը կասկածելիորեն ցույց է տալիս {suspect.name}-ին։")

    # --- Investigator ---
    for a in investigates:
        actor = by_id.get(a.actor_id)
        target = by_id.get(a.target_id)
        if not actor or not target:
            continue
        if a.target_id in disrupted_targets:
            result = "🔎 Այս գիշեր ազդանշանը խափանված էր. արդյունք չհաջողվեց ստանալ։"
        elif precise_investigation:
            result = f"🔎 Ճշգրիտ արդյունք՝ {target.name}-ի վիճակը՝ {_infection_label(target.infection)}։"
        elif vague_only:
            result = random.choice([
                "🔎 Այս գիշեր ամեն ինչ չափազանց մշուշոտ է թվում ստուգելու համար։",
                "🔎 Դու ինչ-որ բան նկատեցիր, բայց չես կարող վստահ լինել։",
            ])
        elif target.role == "zombie" or target.infection in ("infected", "critical"):
            result = "🔎 Այս խաղացողի գործողությունները կասկածելի են։"
        else:
            result = "🔎 Այս խաղացողը կասկածելի բան չի կատարել այս գիշեր։"
        await safe_send(bot, actor.user_id, result)

    # --- Scientist ---
    for a in analyzes:
        actor = by_id.get(a.actor_id)
        target = by_id.get(a.target_id)
        if not actor or not target:
            continue
        if scientist_precise:
            result = f"🔬 Ճշգրիտ վերլուծություն՝ {target.name} — {_infection_label(target.infection)}։"
        else:
            healthy_ish = target.infection in ("healthy", "exposed")
            result = f"🔬 Վերլուծություն՝ {target.name}-ի նմուշը թվում է " + \
                     ("համեմատաբար մաքուր, բայց ամբողջական վստահություն չկա։" if healthy_ish
                      else "մտահոգիչ, արժե հետևել այս խաղացողին։")
        await safe_send(bot, actor.user_id, result)

    # --- Base + bonus evidence discovery ---
    if alive:
        owner = random.choice(alive)
        e = random.choice(EVIDENCE)
        db.add(Evidence(game_id=g.id, round_no=g.round_no, owner_id=owner.id, kind=e["kind"],
                         description=e["description"], misleading=e["misleading"] or misleading_bonus))
        lines.append(f"🧩 Հայտնաբերվեց նոր ապացույց՝ {e['kind']}։")
        if bonus_evidence:
            owner2 = random.choice(alive)
            e2 = random.choice(EVIDENCE)
            db.add(Evidence(game_id=g.id, round_no=g.round_no, owner_id=owner2.id, kind=e2["kind"],
                             description=e2["description"], misleading=e2["misleading"]))
            lines.append(f"🧩 Այս գիշեր հայտնաբերվեց նաև լրացուցիչ ապացույց՝ {e2['kind']}։")

    # --- Radio operator / rescue countdown ---
    g.rescue_eta = max(0, g.rescue_eta - (2 if radio_boost else 1))
    radio_ops = [p for p in alive if p.role == "radio"]
    for p in radio_ops:
        if radio_down:
            await safe_send(bot, p.user_id, "📻 Այս գիշեր ազդանշանը կորավ։ Ոչ մի նոր տեղեկություն։")
        else:
            await safe_send(bot, p.user_id, f"📻 Փրկարարները մոտենում են։ Մոտավորապես {g.rescue_eta} round մնաց մինչև ժամանումը։")

    # --- Morale ---
    if morale_delta:
        for p in alive:
            p.score += morale_delta

    if len(lines) == 5:  # nothing but the header/event line was ever appended
        lines.append("Գիշերը հանգիստ էր, բայց լարվածությունը մնում է։")

    return "\n".join(lines)


def resolve_vote(db, g, players):
    votes = db.query(Vote).filter_by(game_id=g.id, round_no=g.round_no).all()
    alive = [p for p in players if p.alive]
    by_id = {p.id: p for p in players}

    if not votes:
        return "🗳️ ԱՐԴՅՈՒՆՔ\n\nՈչ ոք ձայն չտվեց։ Այս round-ում ոչ ոք չհեռացվեց։"

    counts = Counter(v.target_id for v in votes)
    top_count = max(counts.values())
    top_targets = [tid for tid, c in counts.items() if c == top_count]
    tally_lines = "\n".join(f"{by_id[tid].name} — {c}" for tid, c in counts.most_common())

    if len(top_targets) > 1:
        e = random.choice(EVIDENCE)
        finder = random.choice(alive)
        db.add(Evidence(game_id=g.id, round_no=g.round_no, owner_id=finder.id, kind=e["kind"],
                         description=e["description"], misleading=e["misleading"]))
        return (
            "🗳️ ԱՐԴՅՈՒՆՔ\n\n" + tally_lines +
            "\n\n⚖️ Ձայները հավասար բաժանվեցին. այս round-ում ոչ ոք չհեռացվեց, "
            f"բայց {finder.name}-ը գտավ նոր ապացույց ({e['kind']})։"
        )

    target_id = top_targets[0]
    target = by_id[target_id]
    target.alive = False
    target.score -= 20
    was_zombie = target.role == "zombie"

    reveal = f"\n🎭 Բացահայտված role՝ {ROLES[target.role]['name']}" if g.mode == "classic" else ""
    outcome_line = f"\n\n⚠️ {target.name}-ը հեռացվեց խմբի պաշտպանությունից {top_count} ձայնով։{reveal}"

    if was_zombie:
        for p in alive:
            if p.role != "zombie" and p.id != target_id:
                p.score += 30
        outcome_line += "\n🟢 Ինտուիցիան ճիշտ էր..." if g.mode != "classic" else ""
    else:
        g.wrong_eliminations += 1
        if random.random() < 0.4:
            extra = random.choice([
                "📉 Խմբի morale-ը նվազեց այս սխալ որոշումից հետո։",
                "🔍 Հետագա խուզարկության ժամանակ հայտնաբերվեց նոր, կասկածելի ապացույց։",
                "⚠️ Հաջորդ գիշերը զգացվում է ավելի վտանգավոր։",
            ])
            outcome_line += f"\n{extra}"

    db.add(GameHistory(game_id=g.id, round_no=g.round_no, phase="vote", target=target.name,
                        description=f"Հեռացվեց {top_count} ձայնով (role՝ {target.role})։"))

    return "🗳️ ԱՐԴՅՈՒՆՔ\n\n" + tally_lines + outcome_line


def check_win(g, players):
    alive = [p for p in players if p.alive]
    zombies = [p for p in alive if p.role == "zombie"]
    humans = [p for p in alive if p.role != "zombie"]

    if not zombies:
        return "humans"
    if len(zombies) >= len(humans) or len(humans) <= 2:
        return "zombie"
    if g.rescue_eta <= 0:
        return "zombie"  # rescue arrived too late — the threat was never fully neutralized
    return None


def finish_game(db, g, players, outcome):
    g.phase = "finished"
    g.winner = outcome
    g.phase_until = None
    for p in players:
        if (outcome == "zombie" and p.role == "zombie") or (outcome == "humans" and p.role != "zombie" and p.role != "double_agent"):
            p.score += 100
        if p.alive:
            p.score += 25
        if p.role == "double_agent" and g.wrong_eliminations >= 2:
            p.score += 150
    db.add(GameHistory(game_id=g.id, round_no=g.round_no, phase="ending", description=f"Հաղթող կողմ՝ {outcome}"))


def cinematic(db, g, players, outcome):
    hist = db.query(GameHistory).filter_by(game_id=g.id).order_by(GameHistory.id).all()
    all_votes = db.query(Vote).filter_by(game_id=g.id).all()
    by_id = {p.id: p for p in players}

    lines = [
        "━━━━━━━━━━━━━━━━━━",
        "🧟 ՎԵՐՋԻՆ ԳԻՇԵՐԸ",
        "━━━━━━━━━━━━━━━━━━",
        "",
        "Քաղաքի վրա արևը վերջին անգամ բարձրացավ։ Փրկարարական ուղղաթիռը մոտենում էր,",
        "բայց մինչ այդ պետք էր պարզել վերջին գաղտնիքը՝ ո՞վ էր վարակվածը։",
        "",
        "🎭 ԴԵՐԵՐ",
    ]
    for p in players:
        status = "ողջ" if p.alive else "հեռացված"
        lines.append(f"• {p.name} — {ROLES[p.role]['name']} ({status})")

    lines += [
        "",
        "🏁 ՎԵՐՋՆԱԿԱՆ ԱՐԴՅՈՒՆՔ",
        "🟢 Մարդիկ — " + ("ՀԱՂԹԵՑԻՆ" if outcome == "humans" else "ՊԱՐՏՎԵՑԻՆ"),
        "🔴 Zombie — " + ("ՀԱՂԹԵՑ" if outcome == "zombie" else "ՊԱՐՏՎԵՑ"),
    ]

    # Sharpest suspicion: voter(s) who most often voted for an actual zombie.
    zombie_ids = {p.id for p in players if p.role == "zombie"}
    correct_votes = Counter(v.voter_id for v in all_votes if v.target_id in zombie_ids)
    if correct_votes:
        best_voter_id, n = correct_votes.most_common(1)[0]
        lines.append(f"\n🧠 Ամենաճիշտ կասկածը՝ {by_id[best_voter_id].name} ({n} ճիշտ քվե)")

    # Most convincing bluff: a zombie who survived to the end.
    surviving_zombies = [p for p in players if p.role == "zombie" and p.alive]
    if surviving_zombies:
        lines.append(f"🎭 Ամենահամոզիչ bluff-ը՝ {surviving_zombies[0].name}")

    # Biggest mistake: an innocent eliminated with the most votes in a round.
    innocent_votes = Counter(v.target_id for v in all_votes if v.target_id not in zombie_ids)
    eliminated_innocents = [p for p in players if not p.alive and p.role != "zombie"]
    if eliminated_innocents and innocent_votes:
        worst_id = max((p.id for p in eliminated_innocents), key=lambda pid: innocent_votes.get(pid, 0), default=None)
        if worst_id is not None:
            lines.append(f"💀 Ամենամեծ սխալը՝ {by_id[worst_id].name}-ի դեմ սխալ vote-ը")

    lines += ["", f"⭐ Ամենաբարձր scorer՝ {max(players, key=lambda p: p.score).name}", "", "📜 TIMELINE"]
    for h in hist[-40:]:
        who = f" — {h.target}" if h.target else ""
        lines.append(f"Round {h.round_no} [{h.phase}]: {h.description}{who}")

    lines += ["", "🏆 LEADERBOARD"]
    for p in sorted(players, key=lambda x: x.score, reverse=True):
        lines.append(f"{p.name} — {p.score} ⭐")

    return "\n".join(lines)


async def recover_games(bot=None):
    db = session()
    try:
        for g in db.query(Game).filter(Game.phase.in_(ACTIVE_PHASES)).all():
            if not g.phase_until:
                seconds = {"night": NIGHT_SECONDS, "discussion": DISCUSSION_SECONDS, "vote": VOTE_SECONDS}[g.phase]
                g.phase_until = datetime.utcnow() + timedelta(seconds=seconds)
        db.commit()
        if bot:
            for g in db.query(Game).filter_by(phase="night").all():
                players = db.query(Player).filter_by(game_id=g.id).all()
                await send_night_prompts(bot, db, g, players)
    finally:
        db.close()
