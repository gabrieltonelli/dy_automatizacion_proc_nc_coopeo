import csv
import os
import logging

logger = logging.getLogger(__name__)

class MappingRepository:
    def __init__(self, products_csv: str, branches_csv: str):
        self.products_csv = products_csv
        self.branches_csv = branches_csv
        self.products = {} # Coop Desc -> {code, unit, multiplier}
        self.branches = {} # Branch Prefix -> Finnegans Client Code
        self._load()

    def _load(self):
        # Load Products
        if os.path.exists(self.products_csv):
            with open(self.products_csv, mode='r', encoding='utf-8') as f:
                reader = csv.DictReader(f, delimiter=';')
                for row in reader:
                    self.products[row['DESCRIPCION_COOP']] = {
                        'code': row['CODIGO_FINNEGANS'],
                        'unit': row.get('UNIDAD', 'UN'),
                        'multiplier': float(row.get('MULTIPLO', 1.0) or 1.0)
                    }
        else:
            self._create_products_template()

        # Load Branches
        if os.path.exists(self.branches_csv):
            with open(self.branches_csv, mode='r', encoding='utf-8') as f:
                reader = csv.DictReader(f, delimiter=';')
                for row in reader:
                    self.branches[row['PREFIJO']] = row['CLIENTE_FINNEGANS']
        else:
            self._create_branches_template()

    def get_product(self, desc: str):
        return self.products.get(desc)

    def get_branch_client(self, prefix: str):
        return self.branches.get(prefix)

    def add_missing_products(self, list_of_descs: List[str]):
        """
        Agrega descripciones faltantes al archivo CSV con código Finnegans vacío.
        """
        new_entries = []
        for desc in list_of_descs:
            if desc not in self.products:
                logger.info(f"Nuevo producto detectado: {desc}")
                new_entries.append({'DESCRIPCION_COOP': desc, 'CODIGO_FINNEGANS': ''})
                # Actualizar memoria para evitar duplicados en la misma corrida
                self.products[desc] = {'code': '', 'unit': 'UN', 'multiplier': 1.0}

        if new_entries:
            file_exists = os.path.exists(self.products_csv)
            with open(self.products_csv, mode='a', encoding='utf-8', newline='') as f:
                writer = csv.DictWriter(f, fieldnames=['DESCRIPCION_COOP', 'CODIGO_FINNEGANS'], delimiter=';')
                if not file_exists:
                    writer.writeheader()
                writer.writerows(new_entries)
            logger.info(f"Se agregaron {len(new_entries)} nuevos productos al mapeo.")
        else:
            logger.info("No se detectaron productos nuevos.")

    def _create_products_template(self):
        logger.info(f"Creating products template at {self.products_csv}")
        os.makedirs(os.path.dirname(self.products_csv), exist_ok=True)
        with open(self.products_csv, mode='w', encoding='utf-8', newline='') as f:
            writer = csv.writer(f, delimiter=';')
            writer.writerow(['DESCRIPCION_COOP', 'CODIGO_FINNEGANS'])
            # Ejempos
            writer.writerow(['RAV D/YEYO CARNE X 500 GR L', '10121C'])

    def _create_branches_template(self):
        logger.info(f"Creating branches template at {self.branches_csv}")
        os.makedirs(os.path.dirname(self.branches_csv), exist_ok=True)
        with open(self.branches_csv, mode='w', encoding='utf-8', newline='') as f:
            writer = csv.writer(f, delimiter=';')
            writer.writerow(['PREFIJO', 'CLIENTE_FINNEGANS'])
            # Ejemplos
            writer.writerow(['0270', '15603'])
