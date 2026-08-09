# community/ — Find a Game + the seat-accounting money rule.
#
# The lane that answers "who is actually on this court, and who paid for them".
#
# It exists because two problems turned out to be one. A membership makes court bookings free, but
# nothing knew WHO ELSE was playing — so one membership could cover a second, third or fourth person
# who never paid (two friends, one membership, half price forever). Separately, ~1,100 members often
# have nobody to play with, so courts sit empty. An unpaid second player and an empty seat are the
# same object: a seat nobody has accounted for. Account for seats and the leak closes; publish the
# unaccounted seats and you have Find a Game.
#
# THE SEAT RULE (community/seats.py is the only place it lives):
#   A court booking has SEATS. Every seat is held by a covered member (free), a payer (owes a share),
#   or is OPEN. The court's price for that duration is split equally among the seats that are NOT
#   covered. An OPEN seat unfilled at the cutoff collapses onto the booking holder as a charged seat.
#
# A GAME IS A BOOKING. There is deliberately no parallel `game` table — an open game is a
# diary.booking with visibility='open' and open seats, and a seat is a diary.booking_party row. That
# keeps ONE source of truth for court time and inherits the GiST no-double-book constraint, the diary
# grid, reschedule/cancel, the unified statement, Client-360 and month-end unchanged.
#
# Ships DARK: club.policy.community_enabled + seat_rule_enforced both default false.
