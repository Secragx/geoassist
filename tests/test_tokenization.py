from tokenizers import Tokenizer
from tokenizers.models import WordLevel
from tokenizers.pre_tokenizers import Whitespace

from geoassist.rag.tokenization import XlmRobertaTokenCounter


def test_counts_with_real_tokenizer_file(tmp_path):
    tokenizer = Tokenizer(WordLevel({"[UNK]": 0, "hola": 1, "mundo": 2}, unk_token="[UNK]"))
    tokenizer.pre_tokenizer = Whitespace()
    path = tmp_path / "tokenizer.json"
    tokenizer.save(str(path))
    counter = XlmRobertaTokenCounter(path)
    assert counter.count("hola mundo") == 2
    assert counter.count("") == 0


def test_counts_token_ids_not_words(tmp_path):
    tokenizer = Tokenizer(WordLevel({"[UNK]": 0, "hola": 1, "mundo": 2}, unk_token="[UNK]"))
    tokenizer.pre_tokenizer = Whitespace()
    path = tmp_path / "tokenizer.json"
    tokenizer.save(str(path))

    counter = XlmRobertaTokenCounter(path)

    assert counter.count("hola mundo hola") == 3
    assert counter.count("hola desconocido") == 2
