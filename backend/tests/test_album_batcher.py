from engine.album_batcher import assign_album_indices, chunk_into_albums


def test_chunk_into_albums_ten():
    paths = [f"p{i}.jpg" for i in range(25)]
    chunks = chunk_into_albums(paths, 10)
    assert len(chunks) == 3
    assert len(chunks[0]) == 10
    assert len(chunks[1]) == 10
    assert len(chunks[2]) == 5


def test_assign_album_indices():
    paths = [f"p{i}.jpg" for i in range(12)]
    rows = list(assign_album_indices(paths, 10))
    assert rows[0][2] == 0  # album_index
    assert rows[9][2] == 0
    assert rows[10][2] == 1
