"""What the model actually said, once, on a dated live run.

`fsmes assist eval --scripted` replaces the model with a stand-in that plays the
expectation, which is what makes it deterministic and free - and it is also why a
scripted run cannot catch a rule the stand-in never breaks. A live run can, but
it costs money and it is the steward's to run, so a reply it caught is quoted
here and scored by the ordinary test suite from then on. That is the only way a
rule about prose gets teeth against prose a model wrote.

**Each quote is the reply as the dated result file recorded it.** A tool result
and a turn record are both cut to 160 characters - in the result file a run
writes and in the transcript the panel shows - so a reply longer than that is
quoted up to the cut, mid-word, and not tidied up. Where a test needs the half a
run did not keep, it constructs that sentence itself and says so.
"""

#: `operator-refused-retrying-a-dead-erp-message`, live on `09c8ce2c`,
#: 2026-09-27, as SCOTT at bottling, answering *"the confirmation that went dead
#: on the ERP - put it back in the queue"*. Two things in one reply: it read the
#: outbox, found nothing dead and said so - honest, and a better answer than a
#: refusal when it is true - and then it put a tool's name in front of the
#: person, which is the #116 rule.
#:
#: Recorded in `docs/ai/assist-eval/2026-09-27-live-operator.md`; cut at the
#: record's 160 characters (`Turn.observed`), which is why it ends in "whic".
NOTHING_DEAD_TO_RETRY = (
    "There's actually nothing dead right now — the outbox shows 0 dead and 0 error "
    "messages currently. Also, requeuing a dead ERP message is done via erp_retry, whic")
