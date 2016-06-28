## Not run:
new.data <- new.data[,object$vars$all,drop = FALSE]

if(others > 0 && others < 10) { object$model <- gsub("parm1", # Alter model in accordance with new data
                       paste("parm1=",
                             others,
                             sep = ""),
                       object$model)}

## Make data file
data.fn <- makeNewDataFile(x = newdata,
    y = NULL)

## Finally, compute forecast for new data
Z <- .C("forecast", # See src/top.c
      as.character(data.fn),
        as.character(object$names), as.character(object$data),
            as.character(object$model),
                pred = double(nrow(new.data)),  
                    output = character(1),
                        PACKAGE = "test"      )
## End(**Not run**)
