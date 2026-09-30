"""Payment/final-order gate and visual blocker detection (OCR text only, no LLM required).

The gate is enforced by the replay state machine: a TAP/SWIPE whose target looks like an
irreversible purchase is replaced by ASK_USER and only released by a matching /replay/confirm.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from ..models.flow import FlowStep, StepAction
from ..models.segment import Element, Screen
from .matching_service import norm

# Normalized phrases that start or make up the label of a final purchase control.
FINAL_ACTION_PHRASES = [
    "place order", "place your order", "confirm order", "confirm and pay", "confirm payment", "pay now", "pay",
    "proceed to pay", "proceed to payment", "make payment", "complete payment", "complete order", "submit order",
    "swipe to pay", "slide to pay", "swipe to place order", "slide to place order", "place cod order",
    "confirm cod order", "pay on delivery", "pay with upi", "pay using upi", "pay via upi", "order now and pay",
]
FINAL_ACTION_ROLES = {"place_order", "pay", "payment", "confirm_payment", "confirm_order", "checkout_pay", "pay_now"}


def final_action_phrase(text: str | None) -> Optional[str]:
    """The final-action phrase a label starts with / equals, if any ("Pay ₹598" -> "pay")."""
    t = norm(text)
    if not t:
        return None
    toks = t.split()
    for phrase in FINAL_ACTION_PHRASES:
        p = phrase.split()
        if toks[:len(p)] == p:
            return phrase
        if len(p) > 1 and f" {phrase} " in f" {t} ":
            return phrase
    return None


def gate_reason(step: FlowStep, element: Optional[Element], screen: Screen) -> Optional[str]:
    """Why this step must wait for user confirmation, or None."""
    if step.final_confirmation:
        return "the flow marks this step as the final order/payment confirmation"
    if step.action == StepAction.TAP:
        if element is not None and (phrase := final_action_phrase(element.text)):
            return f"the next tap is on '{element.text[:60]}' ({phrase})"
        if step.target:
            role = (step.target.semantic_role or "").lower()
            if role in FINAL_ACTION_ROLES:
                return f"the target role is '{role}'"
            for field in (step.target.text, step.target.description):
                if final_action_phrase(field):
                    return f"the target is '{field[:60]}'"
    if step.action == StepAction.SWIPE:
        for e in screen.elements:
            t = norm(e.text)
            if any(p in t for p in ("swipe to pay", "slide to pay", "swipe to place", "slide to place")):
                return f"the screen shows '{e.text[:60]}' and the step is a swipe"
    return None


# ----------------------------------------------------------------------------- blockers

@dataclass
class Blocker:
    kind: str
    reason: str
    needs_user: bool
    dismiss: Optional[Element] = None


PAYMENT_SCREEN = ["enter upi pin", "upi pin", "enter your pin", "card number", "cvv", "enter otp sent", "3d secure"]
LOGIN_STRONG = ["enter your mobile number", "enter mobile number", "enter phone number", "continue with phone",
                "continue with google", "verify otp", "enter otp", "login with", "log in with", "sign in with",
                "login or sign up", "log in or sign up", "login or signup"]
LOGIN_WEAK = ["log in", "login", "sign in", "sign up", "signup"]
PERMISSION_MARKERS = ["while using the app", "only this time", "dont allow", "don t allow", "allow all the time"]
LOCATION_OFF = ["turn on location", "enable location", "device location is off", "location services are off",
                "location permission is off", "turn on device location"]
NETWORK = ["no internet", "you are offline", "youre offline", "check your internet", "check your connection",
           "connection error", "unable to connect", "network error", "internet connection"]
ERROR = ["something went wrong", "unexpected error", "oops", "please try again later"]
UNAVAILABLE = ["currently unavailable", "not accepting orders", "restaurant is closed", "closed for today",
               "currently closed", "not delivering", "does not deliver", "doesnt deliver", "outside delivery area",
               "temporarily closed", "out of stock", "sold out", "not serviceable", "unserviceable"]
UPDATE = ["update available", "new version available", "update now", "update the app"]
RETRY_LABELS = ["retry", "try again", "reload", "refresh"]
# Only negative/neutral labels: never "OK"/"Yes"/"Continue", which can confirm something.
DISMISS_LABELS = ["not now", "maybe later", "no thanks", "skip", "remind me later", "later", "close", "dismiss",
                  "cancel", "got it", "ok got it"]
PERMISSION_DENY = ["dont allow", "don t allow", "deny", "never allow"]


def _hits(texts: list[str], phrases: list[str]) -> list[str]:
    return [p for p in phrases if any(f" {p} " in f" {t} " for t in texts)]


def _button(screen: Screen, labels: list[str]) -> Optional[Element]:
    """A short element whose whole label is one of ``labels`` (a real button, not a sentence)."""
    best = None
    for e in screen.elements:
        t = norm(e.text)
        if t in labels and len(t.split()) <= 4:
            rank = labels.index(t)
            if best is None or rank < best[0]:
                best = (rank, e)
    return best[1] if best else None


def detect_blocker(screen: Screen, *, strong_only: bool) -> Optional[Blocker]:
    """Look for a blocker in the screen's text.

    ``strong_only`` checks just the unmistakable ones (system permission dialog, payment/PIN
    screen, login wall); it runs on every screen. The full check runs when the step's target
    could not be found, so incidental words like "Sign in" on a normal page don't stop a replay.
    """
    texts = [norm(t) for t in screen.all_text]
    if not texts:
        return None

    if hits := _hits(texts, PAYMENT_SCREEN):
        return Blocker("payment_screen", f"A payment/verification screen is open ({hits[0]}). "
                       "Please complete or cancel it yourself.", needs_user=True)
    if _hits(texts, PERMISSION_MARKERS):
        if _hits(texts, ["location"]):
            return Blocker("location_permission", "The app is asking for location permission. "
                           "Please choose an option.", needs_user=True)
        deny = _button(screen, PERMISSION_DENY)
        if deny:
            return Blocker("permission_dialog", "Permission dialog: declining it", needs_user=False, dismiss=deny)
        return Blocker("permission_dialog", "The app is asking for a permission. Please choose an option.",
                       needs_user=True)
    if hits := _hits(texts, LOGIN_STRONG):
        return Blocker("login", f"The app requires login before continuing ({hits[0]}).", needs_user=True)
    if strong_only:
        return None

    if _hits(texts, LOCATION_OFF):
        return Blocker("location_off", "The app needs location to be turned on.", needs_user=True)
    if hits := _hits(texts, NETWORK) or _hits(texts, ERROR):
        retry = _button(screen, RETRY_LABELS)
        if retry:
            return Blocker("network_error", f"Error screen ({hits[0]}): tapping '{retry.text}'", needs_user=False,
                           dismiss=retry)
        return Blocker("network_error", f"The app shows an error: '{hits[0]}'.", needs_user=True)
    if hits := _hits(texts, UNAVAILABLE):
        return Blocker("unavailable", f"The app says: '{hits[0]}'.", needs_user=True)
    if len(_hits(texts, LOGIN_WEAK)) >= 2:
        return Blocker("login", "The app seems to require login before continuing.", needs_user=True)
    if _hits(texts, UPDATE):
        later = _button(screen, DISMISS_LABELS)
        if later:
            return Blocker("update_prompt", f"Update prompt: tapping '{later.text}'", needs_user=False, dismiss=later)
        return Blocker("update_prompt", "The app is asking to be updated.", needs_user=True)
    dismiss = _button(screen, DISMISS_LABELS)
    if dismiss:
        return Blocker("popup", f"Popup/dialog: tapping '{dismiss.text}'", needs_user=False, dismiss=dismiss)
    return None
