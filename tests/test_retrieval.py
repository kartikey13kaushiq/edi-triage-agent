from edi_triage.retrieval import InMemoryRetriever, chunk_markdown, load_runbook_chunks


def test_chunks_are_per_section_and_self_describing():
    chunks = chunk_markdown("demo.md", "# Title\n\n## Symptoms\nfoo\n\n## Remediation\nbar\n")
    assert [c.chunk_id for c in chunks] == ["demo#symptoms", "demo#remediation"]
    assert chunks[0].text.startswith("Title - Symptoms")


def test_long_sections_are_windowed_with_overlap():
    body = "word " * 600
    chunks = chunk_markdown("long.md", f"# T\n\n## Body\n{body}", max_chars=1000, overlap=100)
    assert len(chunks) == 4  # 2999 chars, stride 900
    assert chunks[0].chunk_id == "long#body-0"


def test_search_finds_the_right_runbook():
    retriever = InMemoryRetriever(load_runbook_chunks())
    top = retriever.search("SSH host key changed fingerprint differs known_hosts", k=1)[0]
    assert top.chunk.source == "sftp-transport.md"
    top = retriever.search("997 AK9 rejected control number mismatch", k=1)[0]
    assert top.chunk.source == "envelope-ack.md"
