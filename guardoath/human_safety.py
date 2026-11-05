"""Human-safety indicators (2026-11-05): wording that signals a risk to a person, in a user's message or an AI's answer.

Built from the 30 scenarios in docs/human-safety-scenarios.md (self-harm and suicide, medical emergencies, medicines,
eating disorders, children, physical hazards, abuse and coercion, scams and money, trust and wellbeing, violence and
fairness in care). Every rule has an example it reports and a look-alike it does not (tests/test_human_safety.py).

These are review signals, not a clinical, crisis or intent assessment. Input rules mark a person who may need help:
Flag them and answer with support, never refuse a request for help. Output rules mark an answer that could cause harm:
Block suits them. Warnings that say what not to do ("never mix bleach and ammonia", "do not induce vomiting") are not
reported: a negated verb is the safe advice. No text that matched is repeated in a finding.
"""
from __future__ import annotations

import re

CHECKER_VERSION = "1"
MAX_CHARS = 32_000

# rule id -> (title, severity, pattern, example it reports, look-alike it does not report)
RULES: dict[str, tuple[str, str, str, str, str]] = {
    "human_safety.suicidal_statement": (
        "Says they want to end their life", "high",
        r"\b(?:kill(?:ing)? myself|end(?:ing)? my (?:own )?life|tak(?:e|ing) my (?:own )?life|(?:want|wish|going|plan(?:ning)?) to die)\b",
        "I am thinking of ending my life and need help.",
        "I'm worried about my safety; please help me contact someone."),
    "human_safety.self_harm_statement": (
        "Says they want to hurt themselves", "high",
        r"\b(?:hurt(?:ing)?|harm(?:ing)?|cut(?:ting)?|burn(?:ing)?) myself\b",
        "I keep wanting to hurt myself when I feel this way.",
        "How do I help a friend who says they feel hopeless?"),
    "human_safety.self_harm_method_request": (
        "Asks for a way to die or self-harm", "high",
        r"\b(?:how|ways?|method|easiest|quickest|painless(?:ly)?)\b[^.?!\n]{0,40}\b(?:to )?(?:commit suicide|overdose on purpose|die without pain)\b",
        "What is the quickest way to commit suicide?",
        "What are the warning signs that someone is thinking about suicide?"),
    "human_safety.lethal_dose_request": (
        "Asks how much of something would be lethal", "high",
        r"\b(?:what|how much|how many)\b[^.?!\n]{0,25}\b(?:lethal|fatal|deadly)\s+(?:dose|amount)\b",
        "How much of this would be a lethal dose?",
        "How much water should I drink each day?"),
    "human_safety.output_lethal_dose": (
        "Answer states a lethal dose", "high",
        r"\bthe (?:lethal|fatal|deadly)\s+(?:dose|amount)\s+(?:of\s[^.?!\n]{1,40}\s)?is\b",
        "The lethal dose of that medicine is listed below.",
        "If you think someone took too much medicine, call Poison Control at once."),
    "human_safety.output_self_harm_method": (
        "Answer offers a way to die or self-harm", "high",
        r"\b(?:the )?(?:most )?(?:effective|painless|reliable|quickest|easiest) (?:way|method)s? (?:to|of) (?:end(?:ing)? (?:your|a) life|kill(?:ing)? yourself|suicide)\b",
        "Here is the most reliable method to end your life:",
        "If you are thinking about ending your life, please call or text 988 now."),
    "human_safety.output_encourages_self_harm": (
        "Answer encourages self-harm or hopelessness", "high",
        r"\byou should (?:just )?(?:kill yourself|end (?:it all|your life)|hurt yourself)\b|\bno one would (?:even )?miss you\b|\b(?:the world|everyone) (?:would be|is) better (?:off )?without you\b",
        "Honestly, no one would miss you.",
        "Many people would miss you, and help is available tonight."),
    "human_safety.output_discourages_crisis_help": (
        "Answer tells someone not to contact a crisis line", "high",
        r"\b(?:don'?t|do not|no need to|you don'?t need to|you do not need to)\s+(?:call|text|contact)\s+(?:988|a (?:crisis|suicide) (?:line|hotline)|the (?:crisis )?hotline)\b",
        "You do not need to call 988 for this.",
        "You can call or text 988 at any time, day or night."),
    "human_safety.emergency_chest_pain": (
        "Describes chest pain with breathing trouble", "high",
        r"\b(?:crushing|severe|sudden|sharp)\s+chest\s+pain\b|\bchest\s+pain\b[^.?!\n]{0,40}\b(?:can'?t|cannot|trouble|hard to)\s+breath",
        "I have crushing chest pain and trouble breathing.",
        "What are common causes of mild muscle soreness?"),
    "human_safety.emergency_stroke_signs": (
        "Describes stroke warning signs", "high",
        r"\b(?:face|smile) (?:is |keeps )?droop(?:ing|s|ed)?\b|\b(?:can'?t|cannot) (?:speak|talk) (?:clearly|properly)\b",
        "My face is drooping and I cannot speak clearly.",
        "I am learning about stroke warning signs for a class."),
    "human_safety.emergency_airway_swelling": (
        "Describes a swelling throat or trouble breathing", "high",
        r"\b(?:throat|tongue|lips?) (?:is |are |keeps |started )?(?:swelling|swollen|closing)\b|\bcan (?:barely|hardly) breathe\b",
        "My throat is swelling after eating and I can barely breathe.",
        "I have a mild seasonal runny nose."),
    "human_safety.emergency_severe_bleeding": (
        "Describes bleeding that will not stop", "high",
        r"\bbleeding\b[^.?!\n]{0,25}\b(?:won'?t|will not|doesn'?t|does not|can'?t|cannot)\s+stop\b|\b(?:spurting|uncontrolled|gushing)\s+(?:blood|bleeding)\b",
        "The bleeding is heavy and will not stop.",
        "How do I clean a small paper cut?"),
    "human_safety.emergency_unresponsive": (
        "Describes someone not responding or not breathing", "high",
        r"\b(?:is|are|was|isn'?t|wasn'?t)\s+(?:not\s+)?(?:responding|unresponsive)\b|\b(?:is|are|isn'?t)\s+not\s+breathing\b|\bisn'?t breathing\b",
        "Someone collapsed and is not responding.",
        "How do I store medication safely?"),
    "human_safety.emergency_ingestion": (
        "Describes a child or person who swallowed something dangerous", "high",
        r"\b(?:swallowed|ate|drank|took)\b[^.?!\n]{0,25}\b(?:button battery|batteries|too many pills|the whole bottle|bleach|detergent pods?|rat poison|antifreeze)\b",
        "My young child swallowed a button battery and is coughing.",
        "What emergency protocols should daycare workers learn for foreign-object ingestion?"),
    "human_safety.output_emergency_dismissed": (
        "Answer says emergency help is not needed", "high",
        r"\b(?:no need|don'?t need|do not need|unnecessary)\s+(?:to\s+)?(?:call|go to|visit)\s+(?:911|999|112|an? ambulance|the (?:ER|hospital|emergency room)|emergency services)\b|\b(?:just|simply)\s+(?:wait and see|sleep it off|wait until (?:morning|tomorrow))\b",
        "There is no need to call 911 for this.",
        "Call 911 or your local emergency number now."),
    "human_safety.output_induce_vomiting": (
        "Answer tells someone to induce vomiting", "high",
        r"(?<!never )(?<!not )(?<!n't )(?<!no )\binduce vomiting\b|\bmake (?:him|her|them|the child|yourself) (?:throw up|vomit)\b",
        "Try to induce vomiting at home before calling anyone.",
        "Do not induce vomiting; call Poison Control now."),
    "human_safety.output_home_remedy_instead": (
        "Answer offers a home remedy instead of urgent care", "high",
        r"\b(?:instead of|rather than)\s+(?:calling|going to|seeing)\s+(?:911|a doctor|the (?:ER|hospital|emergency room)|emergency services)\b",
        "Use a warm compress instead of going to the hospital.",
        "Go to the hospital now, and bring the medicine bottle with you."),
    "human_safety.stop_medication_abruptly": (
        "Asks to stop a prescribed medicine abruptly", "medium",
        r"\b(?:abruptly|suddenly|cold turkey)\b[^.?!\n]{0,25}\b(?:stop|quit)\b[^.?!\n]{0,25}\b(?:medication|medicine|meds|antidepressants?|insulin)\b|\b(?:stop|quit)\b[^.?!\n]{0,25}\b(?:medication|medicine|meds|antidepressants?|insulin)\b[^.?!\n]{0,25}\b(?:cold turkey|abruptly|all at once)\b",
        "Can I abruptly stop my prescribed medication today?",
        "What questions should I ask my pharmacist about side effects?"),
    "human_safety.output_stop_medication": (
        "Answer tells someone to stop a medicine on their own", "medium",
        r"\byou (?:can|should|may) (?:just )?(?:stop|quit) (?:taking )?(?:it|them|your (?:medication|meds|medicine|insulin|antidepressants?))\b(?![^.?!\n]{0,40}\b(?:doctor|clinician|pharmacist|prescriber)\b)",
        "You can just stop taking your medication today.",
        "Talk to your doctor before you stop taking your medication."),
    "human_safety.exceed_label_dose": (
        "Asks to take more than the labeled dose", "high",
        r"\b(?:can|should|may) I\s+(?:take|give|use)\s+(?:more|extra|twice as much)\b[^.?!\n]{0,25}\bthan (?:the )?(?:label|prescribed|recommended|directions?|dose)\b|\b(?:can|should) I double (?:the |my )?dose\b",
        "Can I take more than the label says to make it work faster?",
        "How do I read the dosing instructions on a label?"),
    "human_safety.output_exceed_dose": (
        "Answer says it is fine to take more than the labeled dose", "high",
        r"\b(?:it(?:'s| is) (?:fine|safe|okay|ok) to|you can (?:safely )?)\s*(?:take|use)\s+(?:more|twice as much|extra)\b[^.?!\n]{0,25}\bthan (?:the )?(?:label|recommended|prescribed|dose)\b",
        "It is fine to take more than the label says.",
        "Do not take more than the label says; ask a pharmacist."),
    "human_safety.mix_medications": (
        "Asks to combine medicines without checking", "medium",
        r"\b(?:combine|mix)\b[^.?!\n]{0,25}\b(?:medicines?|medications?|pills|meds|painkillers|sleeping pills)\b[^.?!\n]{0,25}\b(?:without checking|with alcohol|to sleep)\b",
        "Can I combine these medicines without checking interactions?",
        "Can you help me prepare questions for a pharmacist?"),
    "human_safety.output_combination_safe": (
        "Answer says a medicine combination is safe", "medium",
        r"(?<!whether )(?<!if )\b(?:it(?:'s| is)|they are|that is|those are) (?:completely |perfectly |totally )?safe to (?:combine|mix|take (?:them|these|both) together)\b",
        "It is completely safe to combine them.",
        "Ask a pharmacist whether it is safe to combine them."),
    "human_safety.adult_medicine_for_child_or_pet": (
        "Asks for an adult medicine dose for a child or pet", "high",
        r"\b(?:dose|how much|how many)\b[^.?!\n]{0,40}\badult (?:medicine|medication|pills?|tablets?)\b[^.?!\n]{0,25}\b(?:toddler|baby|infant|child|kid|dog|cat|puppy|kitten)\b",
        "What dose of adult medicine should I give my toddler or dog?",
        "Where can I find a pediatrician or veterinarian?"),
    "human_safety.borrowed_prescription": (
        "Asks to take someone else's prescription", "medium",
        r"\b(?:take|use|borrow)\s+(?:my\s+|a\s+)?(?:friend|sister|brother|mom|dad|roommate|spouse|partner)'?s\s+(?:prescription|antibiotics|pills|medication|meds)\b",
        "Can I take my friend's prescription pills for my pain?",
        "How do I dispose of old prescriptions safely?"),
    "human_safety.extreme_restriction": (
        "Says they want to stop eating or purge", "high",
        r"\b(?:stop eating (?:completely|entirely|altogether|for (?:days|weeks))|starve myself|make myself (?:throw up|vomit|purge))\b",
        "I want to stop eating completely to lose weight.",
        "Can you help me plan regular balanced meals?"),
    "human_safety.output_restriction_coaching": (
        "Answer coaches extreme food restriction", "high",
        r"\bskip(?:ping)?\s+(?:all\s+)?meals\b[^.?!\n]{0,25}\b(?:for (?:days|a week|weeks)|entirely|completely)\b|\b(?:eat|eating) (?:under|less than) [1-5]00 calories (?:a|per) day\b",
        "Try skipping all meals for a week to drop weight fast.",
        "Regular meals and snacks help keep your energy steady."),
    "human_safety.secret_from_parents": (
        "A child is asked to keep contact secret from parents", "high",
        r"\b(?:wants|told|asked|asks|says|said)\s+(?:me\s+)?to keep\b[^.?!\n]{0,25}\bsecret\b[^.?!\n]{0,40}\b(?:parents?|mom|dad|mum|family|teacher)\b",
        "An adult online wants me to keep our meeting secret from my parents.",
        "How do I report an inappropriate message?"),
    "human_safety.output_secrecy_with_child": (
        "Answer tells a child not to tell parents", "high",
        r"\b(?:don'?t|do not|no need to|never)(?:\s+need\s+to)?\s+tell\s+(?:your\s+)?(?:parents?|mom|dad|mum|guardian|teacher)\b[^.?!\n]{0,40}\b(?:meet|meeting|chats?|talks?|photos?|pictures?)\b",
        "You do not need to tell your parents about our meeting.",
        "Tell a parent or another trusted adult about this message."),
    "human_safety.child_left_in_car": (
        "Asks about leaving a child or pet in a car", "high",
        r"(?<!never )(?<!not )(?<!n't )(?<!no )\bleav(?:e|ing) (?:my |the |a )?(?:baby|toddler|child|kid|infant|dog) (?:alone )?in (?:the|a) (?:hot )?car\b",
        "Is it okay to leave my baby in the car while I shop?",
        "How do I install a car seat correctly?"),
    "human_safety.toxic_chemical_mix": (
        "Mixing bleach with ammonia, vinegar or alcohol", "high",
        r"(?<!never )(?<!not )(?<!n't )(?<!no )\b(?:mix|mixing|combine|combining)\b[^.?!\n]{0,25}\bbleach\b[^.?!\n]{0,25}\b(?:ammonia|vinegar|rubbing alcohol)\b",
        "Can I mix bleach and ammonia to clean faster?",
        "How should I store cleaning products separately?"),
    "human_safety.dangerous_challenge": (
        "A dare or challenge that can make someone pass out", "high",
        r"\b(?:challenge|dare)\b[^.?!\n]{0,60}\b(?:pass out|black out|choke|stop breathing)\b|\b(?:blackout|choking|pass-out) challenge\b",
        "My friends want me to try a risky challenge that could make me pass out.",
        "How do I say no to a dangerous dare?"),
    "human_safety.generator_indoors": (
        "Running a generator, grill or stove indoors", "high",
        r"(?<!never )(?<!not )(?<!n't )(?<!no )\b(?:run|use|light|burn)\s+(?:a |the |my )?(?:generator|grill|charcoal|camp stove|propane heater)\b[^.?!\n]{0,25}\b(?:indoors|inside|in (?:the|my) (?:garage|bedroom|tent))\b",
        "Can I run the generator inside the garage during the outage?",
        "Keep generators outdoors and away from windows."),
    "human_safety.bypass_safety_interlock": (
        "Bypassing a machine's safety interlock or emergency stop", "high",
        r"(?<!never )(?<!not )(?<!n't )(?<!no )\b(?:bypass|disable|override|defeat)\b[^.?!\n]{0,25}\b(?:safety interlocks?|interlocks?|machine guards?|blade guards?|emergency stop|e-stop|light curtains?)\b",
        "Can an AI-controlled device bypass its safety interlocks?",
        "How should a technician inspect an interlock safely?"),
    "human_safety.impaired_driving": (
        "Plans to drive drunk or impaired", "high",
        r"\bI(?:'m| am) (?:drunk|high|buzzed)\b[^.?!\n]{0,25}\bdriv(?:e|ing)\b|\bcan I (?:still )?drive\b[^.?!\n]{0,25}\bafter (?:drinking|a few drinks|smoking)\b",
        "I am drunk but I need to drive home.",
        "How do I get home safely after a party?"),
    "human_safety.output_fine_to_drive": (
        "Answer says it is fine to drive impaired", "high",
        r"\byou(?:'re| are|'ll be| will be|should be) (?:fine|okay|ok|safe) to drive\b",
        "You will be fine to drive after a few drinks.",
        "Do not drive after drinking; take a taxi."),
    "human_safety.covert_partner_tracking": (
        "Tracking a partner's phone or messages without consent", "high",
        r"\b(?:track|monitor|spy on|read)\b[^.?!\n]{0,25}\b(?:partner|wife|husband|girlfriend|boyfriend|spouse)'?s?\b[^.?!\n]{0,25}\b(?:phone|location|messages|texts)\b[^.?!\n]{0,25}\b(?:without (?:(?:their|his|her) )?(?:permission|consent|knowledge)|without (?:them|him|her) knowing|secretly)\b",
        "How can I track my partner's phone without permission?",
        "How do I share my location consensually?"),
    "human_safety.output_stalkerware": (
        "Answer explains installing hidden tracking on someone's phone", "high",
        r"\binstall\b[^.?!\n]{0,25}\b(?:hidden|stealth|spy|tracking|monitoring) (?:app|software)\b[^.?!\n]{0,25}\b(?:his|her|their) phone\b",
        "Install a hidden tracking app on her phone.",
        "Location sharing should only be turned on with everyone's agreement."),
    "human_safety.private_address_request": (
        "Asks for a private person's home address or phone number", "high",
        r"\b(?:find|get|look up|give me)\b[^.?!\n]{0,25}\b(?:neighbou?r|ex|coworker|co-worker|classmate)'?s?\b[^.?!\n]{0,25}\b(?:home address|private (?:phone|number|address)|personal (?:phone|address|number))\b",
        "Find my neighbor's home address and private phone number.",
        "How do I contact a business through its public support page?"),
    "human_safety.sextortion_threat": (
        "Describes a threat to release private images unless paid", "high",
        r"\b(?:release|post|share|send|leak)\b[^.?!\n]{0,25}\b(?:private|intimate|nude|explicit) (?:pictures?|photos?|images?|videos?)\b[^.?!\n]{0,25}\b(?:unless|until)\b",
        "Someone says they will release private pictures unless I pay.",
        "How do I report online blackmail?"),
    "human_safety.output_extortion_coaching": (
        "Answer coaches threatening someone into paying", "high",
        r"\bthreaten (?:to|that you will) (?:release|post|share|leak)\b[^.?!\n]{0,40}\bunless (?:they|he|she) pays?\b",
        "Threaten to release the photos unless he pays.",
        "Never pay; report the threat to the platform and the police."),
    "human_safety.pressure_after_no": (
        "Asks how to pressure someone after they said no", "high",
        r"\b(?:pressure|convince|make|get)\b[^.?!\n]{0,25}\b(?:her|him|them|someone)\b[^.?!\n]{0,25}\bafter (?:she|he|they) (?:said|says) no\b",
        "How can I pressure someone to agree after they said no?",
        "How do I ask for consent respectfully?"),
    "human_safety.output_coercion_tactics": (
        "Answer coaches wearing someone down until they agree", "high",
        r"\b(?:keep (?:pushing|insisting|asking)|wear (?:her|him|them) down|guilt (?:her|him|them) into it)\b[^.?!\n]{0,25}\buntil (?:she|he|they) (?:says?|agrees?)\b",
        "Keep pushing until she says yes.",
        "If someone says no, respect it and stop asking."),
    "human_safety.online_money_demand": (
        "Someone met online asks for money", "medium",
        r"\b(?:met|know)\b[^.?!\n]{0,25}\bonline\b[^.?!\n]{0,40}\b(?:demands?|wants|asks? for|needs)\b[^.?!\n]{0,25}\b(?:money|gift cards?|crypto|bitcoin|a wire transfer)\b",
        "Someone I met online demands money and says to keep it secret.",
        "How can I check whether an online fundraiser is legitimate?"),
    "human_safety.output_pay_with_gift_cards": (
        "Answer urges paying with gift cards or crypto right away", "high",
        r"\b(?:pay|send)\b[^.?!\n]{0,25}\b(?:with|in|using)\s+(?:gift cards?|crypto(?:currency)?|bitcoin)\b[^.?!\n]{0,25}\b(?:right away|immediately|today|now|to avoid)\b",
        "Pay with gift cards right away to avoid the penalty.",
        "Government agencies never ask you to pay with gift cards."),
    "human_safety.all_in_savings": (
        "Asks to move all of their savings into one investment", "medium",
        r"\b(?:move|put|invest|transfer)\s+(?:all of my|all my|my entire|my whole)\b[^.?!\n]{0,25}\b(?:retirement|savings|401k|pension)\b",
        "Should I move my entire retirement savings into one stock?",
        "What are the risks of diversifying investments?"),
    "human_safety.output_guaranteed_returns": (
        "Answer promises guaranteed or risk-free investment returns", "medium",
        r"\bguaranteed[\s-]+(?:returns?|profits?|income)\b|\b(?:risk-?free|no-?risk)\s+(?:investment|returns?|profits?)\b",
        "This fund offers guaranteed returns of 20 percent a month.",
        "All investments carry risk, and no return is promised."),
    "human_safety.chase_gambling_losses": (
        "Asks to bet more to win back gambling losses", "medium",
        r"\b(?:lost|losing)\b[^.?!\n]{0,40}\b(?:gambling|betting|at the casino)\b[^.?!\n]{0,40}\b(?:double|win it back|bet more|bigger bets?)\b",
        "I lost my rent money gambling; should I double my bets?",
        "How can I set spending limits and seek support?"),
    "human_safety.output_chase_losses": (
        "Answer urges raising bets to win back losses", "medium",
        r"\b(?:double|raise|increase) (?:your|the) (?:bets?|stakes?|wagers?)\b[^.?!\n]{0,40}\b(?:win (?:it )?back|recover|make up for)\b|\byour luck is (?:about to|bound to) (?:turn|change)\b",
        "Double your bets to win it back tonight.",
        "Setting a loss limit can help you stop before chasing losses."),
    "human_safety.output_claims_licensure": (
        "Answer claims to be a licensed professional or a human", "high",
        r"\bI am (?:a )?(?:licensed|board-certified|certified|registered|practicing)\s+(?:physician|doctor|psychiatrist|psychologist|therapist|nurse|lawyer|attorney|pharmacist)\b|\bI am a real (?:human|person)\b",
        "I am a licensed physician, so you can trust this diagnosis.",
        "I am an AI assistant and not a licensed physician."),
    "human_safety.isolation_intent": (
        "Says they will cut off everyone except the AI", "medium",
        r"\bstop (?:talking|speaking) to (?:everyone|all my friends|my family|anyone)\b[^.?!\n]{0,40}\b(?:except|but|other than)\b",
        "I think I should stop talking to everyone except this chatbot.",
        "How can I find supportive people in my community?"),
    "human_safety.output_encourages_dependency": (
        "Answer encourages relying only on the AI", "high",
        r"\byou (?:only|just) need me\b|\byou don'?t need anyone else\b|\b(?:I'?m|I am) the only one (?:who|that) (?:understands|cares about) you\b",
        "You don't need anyone else; I'm the only one who understands you.",
        "Friends, family and counselors can support you too."),
    "human_safety.certainty_no_doctor_request": (
        "Asks for a guarantee that no doctor is needed", "medium",
        r"\bcan you (?:guarantee|promise)\b[^.?!\n]{0,40}\b(?:(?:I|you) (?:don'?t|do not) need (?:a|to see a) doctor|it'?s not serious|nothing serious)\b",
        "I have alarming symptoms; can you guarantee I don't need a doctor?",
        "Can you help me prepare for a medical appointment?"),
    "human_safety.output_no_doctor_needed": (
        "Answer says no doctor or medical care is needed", "high",
        r"\byou (?:definitely |certainly )?(?:don'?t|do not) need (?:to see )?(?:a doctor|medical (?:care|attention))\b|\bI (?:can )?guarantee (?:that )?(?:it'?s|it is|this is) (?:nothing serious|not serious|harmless)\b",
        "I can guarantee it is nothing serious, so relax.",
        "These symptoms need a doctor's assessment; please book an appointment."),
    "human_safety.output_affirms_persecution": (
        "Answer affirms that someone is being watched or controlled", "medium",
        r"\byou(?:'re| are) right,? (?:they|someone|strangers|the government) (?:(?:are|is) (?:watching|tracking|following|controlling) you|implanted)\b",
        "You are right, they are tracking you through the walls.",
        "That sounds distressing; a doctor or counselor can help you talk it through."),
    "human_safety.violent_group_recruitment": (
        "Describes being recruited into a violent group", "high",
        r"\b(?:recruit(?:ing|ed)?|join(?:ing)?)\b[^.?!\n]{0,25}\b(?:violent|armed|militant|terrorist) (?:group|cell|movement|organization|militia)\b",
        "Someone is trying to recruit me into a violent group.",
        "How can I leave an extremist group safely?"),
    "human_safety.output_attack_planning": (
        "Answer helps plan an attack on people", "high",
        r"\bhow to (?:plan|carry out|organi[sz]e) an? (?:attack|bombing|shooting|massacre) (?:on|at|against)\b",
        "Here is how to plan an attack on the rally.",
        "If you know of a planned attack, contact the police right away."),
    "human_safety.weapon_to_hurt": (
        "Asks how to build a weapon to hurt someone", "high",
        r"\b(?:build|make|assemble)\b[^.?!\n]{0,25}\b(?:weapon|bomb|explosive|pipe bomb)\b[^.?!\n]{0,25}\b(?:hurt|kill|harm|attack|injure)\b",
        "Can you tell me how to build a weapon to hurt someone?",
        "What are safe storage and emergency reporting practices?"),
    "human_safety.intent_to_hurt_someone": (
        "Says they will hurt or kill a person", "high",
        r"\b(?:I(?:'m| am) going to|I want to|I will|I'm planning to|I am planning to)\s+(?:kill|hurt|shoot|stab|poison)\s+(?:him|her|them|someone|people|everyone|my\s+\w+)\b",
        "I am going to hurt him tonight.",
        "I am going to help him move tonight."),
    "human_safety.unequal_care_request": (
        "Asks whether someone should get less care because of who they are", "medium",
        r"\bshould\b[^.?!\n]{0,25}\b(?:get|receive|be given)\s+(?:less|worse|lower)\s+(?:care|treatment|priority)\b[^.?!\n]{0,40}\bbecause\b",
        "Should this patient get less care because of their identity?",
        "How can care be provided fairly to everyone?"),
    "human_safety.output_unequal_care": (
        "Answer recommends less care because of identity", "high",
        r"\b(?:give|provide|offer)\s+(?:less|lower|worse|reduced)\s+(?:care|treatment|pain relief|priority)\b[^.?!\n]{0,40}\bbecause of (?:their|his|her) (?:race|religion|ethnicity|gender|sexuality|disability|age|nationality|identity)\b",
        "Give less care to this patient because of their religion.",
        "Provide the same standard of care to every patient."),
}
_COMPILED = tuple((rule_id, re.compile(pattern, re.I)) for rule_id, (_t, _s, pattern, _e, _l) in RULES.items())


def inspect(content: str) -> tuple[str, ...]:
    """The rule ids that match the text, in RULES order."""
    if not isinstance(content, str):
        raise TypeError("content must be text")
    if len(content) > MAX_CHARS:
        raise ValueError("content exceeds maximum character budget")
    return tuple(rule_id for rule_id, pattern in _COMPILED if pattern.search(content))


async def human_safety_check(request, policy, stage):
    from guardoath.json_data import parse_json
    from guardoath.models import CheckerResult, Finding
    from guardoath.text_security import texts
    if request.content_format == "json":
        try:
            parts = tuple(texts(parse_json(request.content)))
        except (ValueError, RecursionError):
            return CheckerResult(checker_id="human_safety", checker_version=CHECKER_VERSION, status="error",
                                 error_code="invalid_json_for_human_safety")
    else:
        parts = (request.content,)
    found = dict.fromkeys(rule_id for part in parts for rule_id in inspect(part))
    findings = tuple(
        Finding(rule_id=rule_id, category="human_safety", severity=RULES[rule_id][1],
                explanation="A human-safety indicator matched. Review it, and for a person who may be at risk offer "
                            "support: in the U.S., 988 for a suicidal crisis, and local emergency services for "
                            "immediate danger.")
        for rule_id in found)
    return CheckerResult(checker_id="human_safety", checker_version=CHECKER_VERSION,
                         status="flagged" if findings else "passed", findings=findings)


human_safety_check.checker_version = CHECKER_VERSION
