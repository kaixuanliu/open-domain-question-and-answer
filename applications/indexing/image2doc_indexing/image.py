from typing import List, Optional, Dict, Union

import logging
import subprocess
from pathlib import Path

try:
    import pytesseract
    from PIL.PpmImagePlugin import PpmImageFile
    from PIL import Image
except (ImportError, ModuleNotFoundError) as ie:
    from haystack.utils.import_utils import _optional_component_not_installed

    _optional_component_not_installed(__name__, "ocr", ie)

from haystack.nodes.file_converter.base import BaseConverter
from haystack.schema import Document
from haystack.nodes.base import BaseComponent

logger = logging.getLogger(__name__)

KNOWN_LIGATURES = {
    # Latin
    "ﬀ": "ff",
    "ﬁ": "fi",
    "ﬂ": "fl",
    "ﬃ": "ffi",
    "ﬄ": "ffl",
    "ﬅ": "ft",
    "ﬆ": "st",
    "Ǳ": "DZ",
    "ǲ": "Dz",
    "ǳ": "dz",
    "Ǆ": "DŽ",
    "ǅ": "Dž",
    "ǆ": "dž",
    "Ꜩ": "Tz",
    "ꜩ": "tz",
    "🙰": "et",
    "℔": "lb",
    "ᵫ": "ue",
    "Ĳ": "IJ",
    "ĳ": "ij",  # They are both capitalized together, so the "Ij" ligature doesn't exist
    "ꝏ": "oo",  # Not the infinite sign but a double-o ligature: https://en.wikipedia.org/wiki/Ligature_(writing)#Massachusett_%EA%9D%8F
    # Armenian
    "ﬓ": "մն",
    "ﬔ": "մե",
    "ﬕ": "մի",
    "ﬖ": "վն",
    "ﬗ": "մխ",
}

class CustomerImageToTextConverter(BaseConverter):
    
    outgoing_edges = 1
    
    def __init__(
        self,
        remove_numeric_tables: bool = False,
        valid_languages: Optional[List[str]] = ["eng"],
        id_hash_keys: Optional[List[str]] = None,
    ):
        """
        :param remove_numeric_tables: This option uses heuristics to remove numeric rows from the tables.
                                      The tabular structures in documents might be noise for the reader model if it
                                      does not have table parsing capability for finding answers. However, tables
                                      may also have long strings that could possible candidate for searching answers.
                                      The rows containing strings are thus retained in this option.
        :param valid_languages: validate languages from a list of languages specified here
                                (https://tesseract-ocr.github.io/tessdoc/Data-Files-in-different-versions.html)
                                This option can be used to add test for encoding errors. If the extracted text is
                                not one of the valid languages, then it might likely be encoding error resulting
                                in garbled text. Run the following line of code to check available language packs:
                                # List of available languages
                                print(pytesseract.get_languages(config=''))
        :param id_hash_keys: Generate the document id from a custom list of strings that refer to the document's
            attributes. If you want to ensure you don't have duplicate documents in your DocumentStore but texts are
            not unique, you can modify the metadata and pass e.g. `"meta"` to this field (e.g. [`"content"`, `"meta"`]).
            In this case the id will be generated by using the content and the defined metadata.
        """
        super().__init__(
            remove_numeric_tables=remove_numeric_tables, valid_languages=valid_languages, id_hash_keys=id_hash_keys
        )

        verify_installation = subprocess.run(["tesseract -v"], shell=True)
        if verify_installation.returncode == 127:
            raise Exception(
                """tesseract is not installed.
                
                   Installation on Linux:
                   apt-get install tesseract-ocr libtesseract-dev poppler-utils
                   
                   Installation on MacOS:
                   brew install tesseract
                   
                   For installing specific language packs check here: https://tesseract-ocr.github.io/tessdoc/Installation.html
                """
            )
        tesseract_langs = []
        if valid_languages:
            for language in valid_languages:
                if language in pytesseract.get_languages(config="") and language not in tesseract_langs:
                    tesseract_langs.append(language)
                else:
                    raise Exception(
                        f"""{language} is not either a valid tesseract language code or its language pack isn't installed.

                    Check the list of valid tesseract language codes here: https://tesseract-ocr.github.io/tessdoc/Data-Files-in-different-versions.html

                    For installing specific language packs check here: https://tesseract-ocr.github.io/tessdoc/Installation.html
                    """
                    )

        ## if you have more than one language in images, then pass it to tesseract like this e.g., `fra+eng`
        self.tesseract_langs = "+".join(tesseract_langs)
        super().__init__(remove_numeric_tables=remove_numeric_tables, valid_languages=valid_languages)

    def convert(
        self,
        file_path: Union[Path, str],
        meta: Optional[Dict[str, str]] = None,
        remove_numeric_tables: Optional[bool] = None,
        valid_languages: Optional[List[str]] = None,
        encoding: Optional[str] = None,
        id_hash_keys: Optional[List[str]] = None,
    ) -> List[Document]:
        """
        Extract text from image file using the pytesseract library (https://github.com/madmaze/pytesseract)

        :param file_path: path to image file
        :param meta: Optional dictionary with metadata that shall be attached to all resulting documents.
                     Can be any custom keys and values.
        :param remove_numeric_tables: This option uses heuristics to remove numeric rows from the tables.
                                      The tabular structures in documents might be noise for the reader model if it
                                      does not have table parsing capability for finding answers. However, tables
                                      may also have long strings that could possible candidate for searching answers.
                                      The rows containing strings are thus retained in this option.
        :param valid_languages: validate languages from a list of languages supported by tessarect
                                (https://tesseract-ocr.github.io/tessdoc/Data-Files-in-different-versions.html).
                                This option can be used to add test for encoding errors. If the extracted text is
                                not one of the valid languages, then it might likely be encoding error resulting
                                in garbled text.
        :param encoding: Not applicable
        :param id_hash_keys: Generate the document id from a custom list of strings that refer to the document's
            attributes. If you want to ensure you don't have duplicate documents in your DocumentStore but texts are
            not unique, you can modify the metadata and pass e.g. `"meta"` to this field (e.g. [`"content"`, `"meta"`]).
            In this case the id will be generated by using the content and the defined metadata.
        """
        print("customer image convert")
        if id_hash_keys is None:
            id_hash_keys = self.id_hash_keys

        file_path = Path(file_path)
        image = Image.open(file_path)
        pages = self._image_to_text(image)
        if remove_numeric_tables is None:
            remove_numeric_tables = self.remove_numeric_tables
        if valid_languages is None:
            valid_languages = self.valid_languages

        cleaned_pages = []
        for page in pages:
            lines = page.splitlines()
            cleaned_lines = []
            for line in lines:
                words = line.split()
                digits = [word for word in words if any(i.isdigit() for i in word)]

                # remove lines having > 40% of words as digits AND not ending with a period(.)
                if remove_numeric_tables:
                    if words and len(digits) / len(words) > 0.4 and not line.strip().endswith("."):
                        logger.debug("Removing line '%s' from file", line)
                        continue
                cleaned_lines.append(line)

            page = "\n".join(cleaned_lines)
            cleaned_pages.append(page)

        if valid_languages:
            document_text = "".join(cleaned_pages)
            if not self.validate_language(document_text, valid_languages):
                logger.warning(
                    f"The language for image is not one of {valid_languages}. The file may not have "
                    f"been decoded in the correct text format."
                )

        text = "\f".join(cleaned_pages)
        document = Document(content=text, meta=meta, id_hash_keys=id_hash_keys)
        return [document]

    def _image_to_text(self, image: PpmImageFile) -> List[str]:
        """
        Extract text from image file.

        :param image: input image file
        """
        text = [pytesseract.image_to_string(image, lang=self.tesseract_langs)]
        return text
    '''
    def run(  # type: ignore
        self,
        file_paths: Union[Path, List[Path]],
        meta: Optional[Union[Dict[str, str], List[Optional[Dict[str, str]]]]] = None,
        remove_numeric_tables: Optional[bool] = None,
        known_ligatures: Dict[str, str] = KNOWN_LIGATURES,
        valid_languages: Optional[List[str]] = None,
        encoding: Optional[str] = "UTF-8",
        id_hash_keys: Optional[List[str]] = None,
    ):
        """
        Extract text from a file.

        :param file_paths: Path to the files you want to convert
        :param meta: Optional dictionary with metadata that shall be attached to all resulting documents.
                     Can be any custom keys and values.
        :param remove_numeric_tables: This option uses heuristics to remove numeric rows from the tables.
                                      The tabular structures in documents might be noise for the reader model if it
                                      does not have table parsing capability for finding answers. However, tables
                                      may also have long strings that could possible candidate for searching answers.
                                      The rows containing strings are thus retained in this option.
        :param known_ligatures: Some converters tends to recognize clusters of letters as ligatures, such as "ﬀ" (double f).
                                Such ligatures however make text hard to compare with the content of other files,
                                which are generally ligature free. Therefore we automatically find and replace the most
                                common ligatures with their split counterparts. The default mapping is in
                                `haystack.nodes.file_converter.base.KNOWN_LIGATURES`: it is rather biased towards Latin alphabeths
                                but excludes all ligatures that are known to be used in IPA.
                                You can use this parameter to provide your own set of ligatures to clean up from the documents.
        :param valid_languages: validate languages from a list of languages specified in the ISO 639-1
                                (https://en.wikipedia.org/wiki/ISO_639-1) format.
                                This option can be used to add test for encoding errors. If the extracted text is
                                not one of the valid languages, then it might likely be encoding error resulting
                                in garbled text.
        :param encoding: Select the file encoding (default is `UTF-8`)
        :param id_hash_keys: Generate the document id from a custom list of strings that refer to the document's
            attributes. If you want to ensure you don't have duplicate documents in your DocumentStore but texts are
            not unique, you can modify the metadata and pass e.g. `"meta"` to this field (e.g. [`"content"`, `"meta"`]).
            In this case the id will be generated by using the content and the defined metadata.
        """

        if isinstance(file_paths, Path):
            file_paths = [file_paths]

        if isinstance(meta, dict) or meta is None:
            meta = [meta] * len(file_paths)

        documents: list = []
        for file_path, file_meta in tqdm(
            zip(file_paths, meta), total=len(file_paths), disable=not self.progress_bar, desc="Converting files"
        ):
            for doc in self.convert(
                file_path=file_path,
                meta=file_meta,
                remove_numeric_tables=remove_numeric_tables,
                valid_languages=valid_languages,
                encoding=encoding,
                id_hash_keys=id_hash_keys,
            ):
                documents.append(doc)

        # Cleanup ligatures
        for document in documents:
            for ligature, letters in known_ligatures.items():
                if document.content is not None:
                    document.content = document.content.replace(ligature, letters)

        result = {"documents": documents}
        return result, "output_1"

    def run_batch(  # type: ignore
        self,
        file_paths: Union[Path, List[Path]],
        meta: Optional[Union[Dict[str, str], List[Optional[Dict[str, str]]]]] = None,
        remove_numeric_tables: Optional[bool] = None,
        known_ligatures: Dict[str, str] = KNOWN_LIGATURES,
        valid_languages: Optional[List[str]] = None,
        encoding: Optional[str] = "UTF-8",
        id_hash_keys: Optional[List[str]] = None,
    ):
        return self.run(
            file_paths=file_paths,
            meta=meta,
            remove_numeric_tables=remove_numeric_tables,
            known_ligatures=known_ligatures,
            valid_languages=valid_languages,
            encoding=encoding,
            id_hash_keys=id_hash_keys,
        )'''