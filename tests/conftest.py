import pytest

from esg_lens.config import Settings
from esg_lens.llm.fake import FakeProvider
from esg_lens.sample import build_sample_report
from esg_lens.store import DocumentStore


@pytest.fixture()
def settings(tmp_path):
    return Settings(provider="fake", data_dir=str(tmp_path / "store"))


@pytest.fixture()
def llm():
    return FakeProvider()


@pytest.fixture()
def sample_pdf(tmp_path):
    return build_sample_report(tmp_path / "sample_esg_report.pdf")


@pytest.fixture()
def indexed(settings, llm, sample_pdf):
    store = DocumentStore(settings, llm)
    doc_id = store.add_pdf(sample_pdf)
    index, pages = store.load(doc_id)
    return store, doc_id, index, pages
