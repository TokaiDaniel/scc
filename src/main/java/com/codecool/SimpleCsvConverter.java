package com.codecool;

import java.io.*;
import java.util.ArrayList;
import java.util.Arrays;

public class SimpleCsvConverter {
    private File file;
    private final OutputFormat outputFormat;

    public SimpleCsvConverter(File file, OutputFormat outputFormat) {
        this.file = file;
        this.outputFormat = outputFormat;
    }

    public void convert() throws IOException {
        System.out.println("I convert CSV to " + outputFormat);
        for (String line: readData()) {
            System.out.println(line);
        }
    }

    public ArrayList<String> readData() throws IOException {

        BufferedReader csvReader = new BufferedReader(new FileReader(file));
        String row;
        ArrayList<String> data = new ArrayList<>();
        while ((row = csvReader.readLine()) != null) {
            data.add(Arrays.toString(row.split(",")));
        }
        csvReader.close();
        return data;
    }
}
