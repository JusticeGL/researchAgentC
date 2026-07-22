"""实验树测试。见 IMPLEMENTATION-P2.md §4.2、§6。"""
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture
def tree_db(tmp_path):
    from loop import tree

    p = tmp_path / "tree.sqlite"
    tree.init_db(p)
    return p


def _record_run(db_path, base_run_fields, **ov):
    from core import results

    return results.record_run(db=db_path, metrics={"acc": 0.8},
                              **base_run_fields(**ov))


def test_add_and_get_node(tree_db, db_path, base_run_fields):
    from loop import tree

    rid = _record_run(db_path, base_run_fields)
    nid = tree.add_node(tree_db, contract_id="c_test", change_description="加 CSP",
                        patch="+ csp", run_ids=[rid], dev_score=0.8,
                        results_path=db_path)
    node = tree.get_node(tree_db, nid)
    assert node.run_ids == [rid]
    assert node.status == "ok"
    assert node.dev_score == 0.8


def test_node_requires_nonempty_run_ids(tree_db):
    from loop import tree

    with pytest.raises(ValueError):
        tree.add_node(tree_db, contract_id="c_test", change_description="空",
                      patch="", run_ids=[])


def test_node_run_id_must_exist_in_runs(tree_db, db_path):
    from loop import tree

    with pytest.raises(ValueError):
        tree.add_node(tree_db, contract_id="c_test", change_description="幽灵",
                      patch="", run_ids=["does_not_exist"], results_path=db_path)


def test_tree_is_append_only(tree_db, db_path, base_run_fields):
    import sqlite3

    from loop import tree

    rid = _record_run(db_path, base_run_fields)
    nid = tree.add_node(tree_db, contract_id="c_test", change_description="x",
                        patch="", run_ids=[rid], results_path=db_path)
    con = sqlite3.connect(tree_db)
    with pytest.raises((sqlite3.IntegrityError, sqlite3.OperationalError)):
        con.execute("DELETE FROM tree_nodes WHERE node_id=?", (nid,))
        con.commit()
    con.close()


def test_status_and_expansion_update(tree_db, db_path, base_run_fields):
    from loop import tree

    rid = _record_run(db_path, base_run_fields)
    nid = tree.add_node(tree_db, contract_id="c_test", change_description="x",
                        patch="", run_ids=[rid], results_path=db_path)
    tree.set_status(tree_db, nid, "fluke")
    tree.increment_expansion(tree_db, nid)
    node = tree.get_node(tree_db, nid)
    assert node.status == "fluke"
    assert node.expansion_count == 1


def test_ancestors_and_siblings(tree_db, db_path, base_run_fields):
    from loop import tree

    r0 = _record_run(db_path, base_run_fields, seed=0)
    r1 = _record_run(db_path, base_run_fields, seed=1)
    r2 = _record_run(db_path, base_run_fields, seed=2)
    root = tree.add_node(tree_db, "c_test", "root", "", [r0], results_path=db_path)
    child = tree.add_node(tree_db, "c_test", "child", "", [r1],
                          parent_node_id=root, results_path=db_path)
    tree.add_node(tree_db, "c_test", "sib", "", [r2],
                  parent_node_id=root, results_path=db_path)
    anc = tree.ancestors(tree_db, child)
    assert [n.node_id for n in anc] == [root]
    sibs = tree.siblings(tree_db, child)
    assert len(sibs) == 1


def test_dev_score_never_in_render_or_paper():
    """dev_score 是搜索内部量，绝不能出现在渲染器或论文里（不进论文）。"""
    render_src = (REPO_ROOT / "core" / "render.py").read_text(encoding="utf-8")
    assert "dev_score" not in render_src
    paper_dir = REPO_ROOT / "paper"
    if paper_dir.exists():
        for p in paper_dir.rglob("*"):
            if p.is_file() and p.suffix in (".md", ".tex", ".txt"):
                assert "dev_score" not in p.read_text(encoding="utf-8"), p
