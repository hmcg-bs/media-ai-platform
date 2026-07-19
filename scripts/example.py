import replicate

MODEL = "qwen/qwen-image-layered"

class ImageLayerModel():

    def init(self):

        self.model = model
       
        self.description = description 
        self.output_format = output_format
        self.output_quality = output_quality 
        self.image = image 

    def run(self, go_fast : bool
                , num_layers : int ):

        return replicate.run(
            MODEL,
    input={
        "image": "https://replicate.delivery/pbxt/OGJVQUlkQmQSozG4vxiA5NKCveSE3mAJDppBHrs1Smp2G23p/office-split.png",
        "go_fast": True,
        "num_layers": num_layers,
        "description": "auto",
        "output_format": "webp",
        "output_quality": 95
    }

        )

output = replicate.run(
    MODEL,
    input={
        "image": "https://replicate.delivery/pbxt/OGJVQUlkQmQSozG4vxiA5NKCveSE3mAJDppBHrs1Smp2G23p/office-split.png",
        "go_fast": True,
        "num_layers": 4,
        "description": "auto",
        "output_format": "webp",
        "output_quality": 95
    }
)

# To access the file URL:
print(output[0].url)
#=> "http://example.com"

# To write the file to disk:
with open("my-image.png", "wb") as file:
    file.write(output[0].read())