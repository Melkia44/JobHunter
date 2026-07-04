from job_hunter.scoring.hard_skills import score_hard_skills


def test_weighted_sum():
    # pilotage 8 + itil 15 + sla 10 + delivery 15
    assert score_hard_skills("Pilotage ITIL, respect des SLA, delivery de services") == 48


def test_cap_at_100():
    text = (
        "itil delivery infogérance sla kpi incident copil télécom b2b 5g "
        "aws python etl airflow mongo management d'équipe pmo"
    )
    assert score_hard_skills(text) == 100


def test_no_hits():
    assert score_hard_skills("Développeur Java backend") == 0


def test_word_boundaries():
    assert score_hard_skills("awsome slam") == 0  # ni 'aws' ni 'sla' en sous-chaîne
